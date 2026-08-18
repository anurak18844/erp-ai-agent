from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, Field

from config.settings import get_settings


REQUIRED_KEYS = {"collection", "description", "fields", "relationships", "business_rules", "notes"}


class MetadataCandidate(BaseModel):
    collection: str
    score: float
    selected: bool
    reason: str
    source_file: str


class MetadataSearchResult(BaseModel):
    candidates: list[MetadataCandidate]
    metadata_context: dict[str, dict[str, Any]] = Field(default_factory=dict)


class MetadataRetriever(Protocol):
    def search(self, query: str, *, limit: int = 4) -> MetadataSearchResult: ...


def _terms(text: str) -> set[str]:
    normalized = re.sub(r"[_\-/]", " ", text.casefold())
    words = set(re.findall(r"[\wก-๙]+", normalized, flags=re.UNICODE))
    # Thai commonly has no spaces; short concepts improve substring matching.
    concepts = {
        "เช่า", "รถ", "ลูกค้า", "ชำระ", "ค้าง", "ซ่อม", "โปรโมชั่น",
        "เพิ่ม", "ลด", "วัน", "สาเหตุ", "ทะเบียน", "คืน", "ผู้เช่า",
        "อุบัติเหตุ", "เหตุ", "เสียหาย", "ชาร์จ", "แบตเตอรี่", "ค่าไฟ", "พลังงาน",
        "ผ่อน", "งวด", "กำหนด", "แคมเปญ", "การตลาด", "รายได้", "conversion", "roi",
        "rental", "customer", "payment", "maintenance", "promotion", "vehicle",
        "adjustment", "reason", "license", "plate", "incident", "charging", "installment",
        "campaign", "engagement", "revenue", "energy", "battery",
    }
    return words | {term for term in concepts if term in normalized}


class MetadataCatalog:
    def __init__(self, metadata_dir: Path | str | None = None):
        self.metadata_dir = Path(metadata_dir or get_settings().metadata_dir)
        self._documents: dict[str, dict[str, Any]] | None = None
        self._sources: dict[str, str] = {}

    def load(self, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
        if self._documents is not None and not refresh:
            return self._documents
        documents: dict[str, dict[str, Any]] = {}
        sources: dict[str, str] = {}
        for path in sorted(self.metadata_dir.glob("*.yaml")):
            with path.open("r", encoding="utf-8") as stream:
                data = yaml.safe_load(stream)
            if not isinstance(data, dict):
                raise ValueError(f"Metadata must be a mapping: {path.name}")
            missing = REQUIRED_KEYS - data.keys()
            if missing:
                raise ValueError(f"Metadata {path.name} missing keys: {sorted(missing)}")
            collection = str(data["collection"])
            if collection in documents:
                raise ValueError(f"Duplicate metadata collection: {collection}")
            documents[collection] = data
            sources[collection] = str(path.relative_to(self.metadata_dir.parent)).replace("\\", "/")
        if not documents:
            raise ValueError(f"No metadata YAML found in {self.metadata_dir}")
        self._documents, self._sources = documents, sources
        return documents

    @property
    def collections(self) -> set[str]:
        return set(self.load())

    def get(self, collection: str) -> dict[str, Any] | None:
        return self.load().get(collection)

    def source(self, collection: str) -> str:
        self.load()
        return self._sources.get(collection, "")

    def search(self, query: str, *, limit: int = 6) -> MetadataSearchResult:
        query_terms = _terms(query)
        normalized_query = query.casefold()
        candidates: list[MetadataCandidate] = []
        docs = self.load()
        raw_scores: dict[str, float] = {}
        reasons: dict[str, list[str]] = {}
        for collection, data in docs.items():
            score = 0.0
            hits: list[str] = []
            discovery_text = " ".join([
                collection,
                str(data["description"]),
                " ".join(map(str, data.get("aliases", []))),
                " ".join(map(str, data.get("search_terms", []))),
            ])
            collection_terms = _terms(discovery_text)
            common = query_terms & collection_terms
            if common:
                score += 3.0 * len(common)
                hits.append("collection description")
            explicit_aliases = [collection, *map(str, data.get("aliases", []))]
            alias_hits = [
                alias for alias in explicit_aliases
                if alias.casefold() in normalized_query
            ]
            if alias_hits:
                score += 8.0 * len(alias_hits)
                hits.append("explicit alias")
            for field, spec in data["fields"].items():
                field_terms = _terms(field + " " + str(spec.get("description", "")))
                overlap = query_terms & field_terms
                if overlap:
                    score += 2.0 * len(overlap)
                    hits.append(f"field {field}")
            rule_text = " ".join(map(str, data["business_rules"]))
            rule_overlap = query_terms & _terms(rule_text)
            if rule_overlap:
                score += 1.5 * len(rule_overlap)
                hits.append("business rule")
            relationship_text = " ".join(str(item) for item in data["relationships"])
            rel_overlap = query_terms & _terms(relationship_text)
            if rel_overlap:
                score += len(rel_overlap)
                hits.append("relationship")
            raw_scores[collection], reasons[collection] = score, hits
        max_score = max(raw_scores.values(), default=1.0) or 1.0
        ranked = sorted(raw_scores, key=lambda name: (-raw_scores[name], name))
        selected_names = {name for name in ranked[:limit] if raw_scores[name] > 0}
        # Multi-collection questions can need a related collection just below
        # the lexical rank limit. Include relevant one-hop relationship targets.
        relationship_neighbors = {
            rel.get("target_collection")
            for name in selected_names
            for rel in docs[name].get("relationships", [])
            if rel.get("target_collection") in docs
            and raw_scores.get(rel.get("target_collection"), 0) > 0
        }
        selected_names.update(relationship_neighbors)
        nearby_ranked = set(ranked[:limit + 2])
        reverse_relationship_neighbors = {
            name
            for name, data in docs.items()
            if name in nearby_ranked
            and raw_scores.get(name, 0) > 0
            and any(
                rel.get("target_collection") in selected_names
                for rel in data.get("relationships", [])
            )
        }
        selected_names.update(reverse_relationship_neighbors)
        for name in ranked:
            score = round(raw_scores[name] / max_score, 3)
            hits = list(dict.fromkeys(reasons[name]))
            candidates.append(MetadataCandidate(
                collection=name,
                score=score,
                selected=name in selected_names,
                reason=("Matched " + ", ".join(hits)) if hits else "No relevant metadata terms matched",
                source_file=self.source(name),
            ))
        context = {name: docs[name] for name in selected_names}
        return MetadataSearchResult(candidates=candidates, metadata_context=context)


def search_metadata(query: str, *, limit: int = 6, catalog: MetadataCatalog | None = None) -> dict[str, Any]:
    return (catalog or MetadataCatalog()).search(query, limit=limit).model_dump()
