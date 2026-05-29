from __future__ import annotations

import csv
import difflib
import html
import json
import os
import platform
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from gas_server.core.config import DATA_DIR, ensure_runtime_dirs
from gas_server.core.geo_agent import GeoAgent, ProgressCallback
from gas_server.core.llm_client import build_llm_client, format_service_name


ensure_runtime_dirs()


CATEGORIES = [
    "Unlawful detention",
    "Human trafficking",
    "Enslavement",
    "Willful killing of civilians",
    "Mass execution",
    "Kidnapping",
    "Extrajudicial killing",
    "Forced disappearance",
    "Damage or destruction of civilian critical infrastructure",
    "Damage or destruction, looting, or theft of cultural heritage",
    "Military operations (battle, shelling)",
    "Gender-based or other conflict-related sexual violence",
    "Violent crackdowns on protesters/opponents/civil rights abuse",
    "Indiscriminate use of weapons",
    "Torture or indications of torture",
    "Persecution based on political, racial, ethnic, gender, or sexual orientation",
    "Movement of military, paramilitary, or other troops and equipment",
]

FOCUS_CATEGORIES = [
    "Military operations (battle, shelling)",
    "Damage or destruction of civilian critical infrastructure",
    "Indiscriminate use of weapons",
    "Willful killing of civilians",
    "Mass execution",
    "Movement of military, paramilitary, or other troops and equipment",
]

OUTPUT_COLUMNS = [
    "event_id",
    "location",
    "date",
    "category",
    "description",
    "evidence_quote",
    "latitude",
    "longitude",
    "source_name",
    "source_url",
    "geocode_status",
    "validation_status",
]

_REGION_NAMES = {
    "africa",
    "asia",
    "europe",
    "latin america",
    "middle east",
    "north africa",
    "south asia",
    "sub-saharan africa",
    "world",
}

_COUNTRY_ABBREVIATIONS = {
    "drc": "Democratic Republic of the Congo",
    "d.r.c.": "Democratic Republic of the Congo",
    "uae": "United Arab Emirates",
    "u.a.e.": "United Arab Emirates",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "us": "United States",
    "u.s.": "United States",
    "usa": "United States",
    "u.s.a.": "United States",
}

_FIELD_ALIASES = {
    "location": {
        "location",
        "place",
        "placename",
        "incidentlocation",
        "eventlocation",
        "adminlocation",
        "where",
    },
    "date": {
        "date",
        "eventdate",
        "incidentdate",
        "datetime",
        "time",
        "publishedat",
        "publisheddate",
    },
    "category": {
        "category",
        "eventcategory",
        "incidentcategory",
        "type",
        "eventtype",
        "classification",
    },
    "description": {
        "description",
        "summary",
        "eventdescription",
        "incidentdescription",
        "details",
        "text",
    },
    "evidence_quote": {
        "evidencequote",
        "evidence",
        "quote",
        "sourcequote",
        "supportingquote",
    },
    "latitude": {
        "latitude",
        "lat",
        "y",
    },
    "longitude": {
        "longitude",
        "lon",
        "lng",
        "long",
        "x",
    },
    "source_name": {
        "sourcename",
        "source",
        "publisher",
        "outlet",
    },
    "source_url": {
        "sourceurl",
        "url",
        "link",
        "articleurl",
    },
}


def _normalize_column_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _canonical_column(column: Any) -> str | None:
    normalized = _normalize_column_name(column)
    for field, aliases in _FIELD_ALIASES.items():
        if normalized in aliases:
            return field
    return None


def _clean_cell(value: Any) -> str:
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_location(location: str) -> str:
    parts = [re.sub(r"\s+", " ", part).strip(" .") for part in str(location or "").split(",")]
    parts = [part for part in parts if part]
    if not parts:
        return _normalize_spaces(location)

    last_key = parts[-1].lower()
    if last_key in _COUNTRY_ABBREVIATIONS:
        parts[-1] = _COUNTRY_ABBREVIATIONS[last_key]

    deduped = [parts[0]]
    for part in parts[1:]:
        if part.lower() != deduped[-1].lower():
            deduped.append(part)
    return ", ".join(deduped)


def _clean_location_for_geocoding(location: str) -> str:
    parts = [part.strip() for part in str(location or "").split(",") if part.strip()]
    cleaned = [part for part in parts if part.lower() not in _REGION_NAMES]
    if not cleaned:
        cleaned = parts
    if cleaned:
        last_key = cleaned[-1].lower()
        if last_key in _COUNTRY_ABBREVIATIONS:
            cleaned[-1] = _COUNTRY_ABBREVIATIONS[last_key]
    return ", ".join(cleaned)


def _location_key(location: str) -> str:
    return _normalize_location(location).lower()


def _normalize_date(date_text: str | None) -> str | None:
    if date_text is None:
        return None
    text = str(date_text).strip()
    if not text:
        return None

    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d")
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
    ):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text


def _extract_json_payload(text: str) -> str:
    start_candidates = [index for index in (text.find("["), text.find("{")) if index != -1]
    if not start_candidates:
        return text
    start = min(start_candidates)
    stack: list[str] = []
    for index, char in enumerate(text[start:], start=start):
        if char in "[{":
            stack.append(char)
        elif char in "]}":
            if not stack:
                continue
            opening = stack.pop()
            if (opening == "[" and char != "]") or (opening == "{" and char != "}"):
                return text
            if not stack:
                return text[start : index + 1]
    return text[start:]


def _parse_llm_json(raw: str) -> list[dict[str, Any]]:
    if not raw:
        return []
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    payload = _extract_json_payload(text)

    parsed: Any
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        repaired = re.sub(r",\s*([}\]])", r"\1", payload)
        try:
            parsed = json.loads(repaired)
        except json.JSONDecodeError:
            repaired = repaired.replace("'", '"')
            try:
                parsed = json.loads(repaired)
            except json.JSONDecodeError:
                return []

    if isinstance(parsed, dict):
        for key in ("incidents", "events", "records"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
        else:
            parsed = [parsed]

    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _category_lookup() -> dict[str, str]:
    return {category.lower(): category for category in CATEGORIES}


def _normalize_category(category: str) -> tuple[str, bool]:
    text = _normalize_spaces(category)
    if not text:
        return "", False
    lookup = _category_lookup()
    if text.lower() in lookup:
        return lookup[text.lower()], True

    lower = text.lower()
    keyword_map = [
        (("shelling", "battle", "airstrike", "strike", "combat", "military operation"), "Military operations (battle, shelling)"),
        (("infrastructure", "power plant", "hospital", "water", "bridge", "critical"), "Damage or destruction of civilian critical infrastructure"),
        (("indiscriminate", "cluster", "missile", "drone", "rocket"), "Indiscriminate use of weapons"),
        (("civilian killed", "civilians killed", "killing of civilians"), "Willful killing of civilians"),
        (("mass execution", "executed", "execution"), "Mass execution"),
        (("troop", "convoy", "equipment", "movement", "deployment"), "Movement of military, paramilitary, or other troops and equipment"),
        (("kidnap", "abduct"), "Kidnapping"),
        (("detention", "detained"), "Unlawful detention"),
        (("torture",), "Torture or indications of torture"),
        (("sexual violence", "gender-based"), "Gender-based or other conflict-related sexual violence"),
    ]
    for keywords, mapped in keyword_map:
        if any(keyword in lower for keyword in keywords):
            return mapped, True

    close = difflib.get_close_matches(text, CATEGORIES, n=1, cutoff=0.72)
    if close:
        return close[0], True
    return text, False


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _valid_coordinates(latitude: Any, longitude: Any) -> tuple[float | None, float | None]:
    lat = _coerce_float(latitude)
    lon = _coerce_float(longitude)
    if lat is None or lon is None:
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return lat, lon


def _is_garbage_location(location: str) -> bool:
    loc = str(location or "").lower().strip()
    if not loc:
        return True
    reject = ("not specified", "unknown", "n/a", "unspecified", "none", "various")
    return any(item == loc or item in loc for item in reject)


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _is_grounded_quote(article_text: str, quote: str) -> bool:
    normalized_quote = _normalize_for_match(quote)
    if not normalized_quote:
        return True
    if len(normalized_quote) < 15:
        return False
    return normalized_quote in _normalize_for_match(article_text)


def _split_category_description(parts: list[str]) -> tuple[str, str]:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    if not cleaned:
        return "", ""
    for end in range(1, len(cleaned) + 1):
        candidate = ", ".join(cleaned[:end])
        normalized, valid = _normalize_category(candidate)
        if valid:
            return normalized, ", ".join(cleaned[end:])
    return cleaned[0], ", ".join(cleaned[1:])


def _looks_like_date(value: str) -> bool:
    text = str(value or "").strip()
    if re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", text):
        return True
    if re.search(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", text):
        return True
    return bool(re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", text, flags=re.IGNORECASE))


def _repair_loose_event_row(header: list[str], row: list[str]) -> list[str]:
    canonical = [_canonical_column(column) for column in header]
    expected = ["location", "date", "category", "description", "latitude", "longitude"]
    if len(header) >= 6 and canonical[:6] == expected and len(row) > len(header):
        date_index = next((index for index, value in enumerate(row[:-3]) if _looks_like_date(value)), None)
        if date_index and date_index > 0:
            location = ", ".join(part.strip() for part in row[:date_index] if part.strip())
            category, description = _split_category_description(row[date_index + 1 : -2])
            return [location, row[date_index].strip(), category, description, row[-2].strip(), row[-1].strip()]

    if len(row) > len(header) and canonical and canonical[0] == "location":
        extra = len(row) - len(header)
        return [", ".join(part.strip() for part in row[: extra + 1])] + row[extra + 1 :]
    if len(row) > len(header):
        return row[: len(header) - 1] + [", ".join(row[len(header) - 1 :])]
    return row + [""] * (len(header) - len(row))


def _read_csv_table(path: Path) -> pd.DataFrame:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle, delimiter=delimiter))
    if not rows:
        return pd.DataFrame()
    header = [column.strip() for column in rows[0]]
    fixed_rows = [_repair_loose_event_row(header, row) for row in rows[1:] if any(cell.strip() for cell in row)]
    return pd.DataFrame(fixed_rows, columns=header)


def _read_json_table(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
        for feature in payload.get("features", []):
            if not isinstance(feature, dict):
                continue
            properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
            record = dict(properties)
            geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
            coordinates = geometry.get("coordinates")
            if geometry.get("type") == "Point" and isinstance(coordinates, list) and len(coordinates) >= 2:
                record.setdefault("longitude", coordinates[0])
                record.setdefault("latitude", coordinates[1])
            records.append(record)
    elif isinstance(payload, dict):
        for key in ("events", "incidents", "records", "data"):
            if isinstance(payload.get(key), list):
                records = [item for item in payload[key] if isinstance(item, dict)]
                break
        else:
            records = [payload]
    elif isinstance(payload, list):
        records = [item for item in payload if isinstance(item, dict)]
    return pd.DataFrame(records)


class SpatiotemporalConflictEventAgent(GeoAgent):
    agent_id = "spatiotemporal_conflict_event_agent"
    agent_name = "Spatiotemporal Conflict Event Layer Agent"
    agent_version = "1.0.0"
    agent_description = (
        "A GMU/STC agent that transforms unstructured conflict reports or structured event "
        "tables into standardized, GIS-ready spatiotemporal event layers."
    )
    requires_input_datasets = False
    requires_model_credentials = False

    def __init__(self, api_key: str | None = None, model: str | None = None):
        super().__init__(
            api_key=api_key,
            model=model or "gpt-5.2",
            output_dir=DATA_DIR / self.agent_id,
        )
        self.service_name = format_service_name(self.agent_name)
        self.gibd_api_key = os.getenv("GIBD_API_KEY")
        self.client = build_llm_client(
            service_name=self.service_name,
            openai_api_key=self.api_key,
            gibd_api_key=self.gibd_api_key,
        )

    def _parse_llm_json(self, raw: str) -> list[dict[str, Any]]:
        return _parse_llm_json(raw)

    def _artifact_run_dir(self, query: str) -> Path:
        out_dir = Path(getattr(self, "output_dir", DATA_DIR / self.agent_id))
        stem_words = re.findall(r"[a-z0-9]+", (query or "").lower())[:2]
        stem = "_".join(stem_words) or "conflict_events"
        run_dir = out_dir / f"{stem}_{uuid.uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def _article_text_from_query(self, query: str) -> str:
        match = re.search(r"\barticle\s*:\s*(.*)", query or "", flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
        return (query or "").strip()

    def _read_input_dataset(self, dataset_path: str) -> tuple[pd.DataFrame | None, str | None]:
        path = Path(dataset_path)
        suffix = path.suffix.lower()
        if suffix in {".csv", ".tsv"}:
            return _read_csv_table(path), None
        if suffix in {".json", ".geojson"}:
            return _read_json_table(path), None
        if suffix in {".txt", ".md"}:
            return None, path.read_text(encoding="utf-8")
        if suffix in {".gpkg", ".shp", ".gml", ".kml"}:
            import geopandas as gpd

            gdf = gpd.read_file(path)
            df = pd.DataFrame(gdf.drop(columns=["geometry"], errors="ignore"))
            if "geometry" in gdf and not gdf.empty:
                points = gdf.geometry
                df["longitude"] = [geom.x if geom is not None and geom.geom_type == "Point" else None for geom in points]
                df["latitude"] = [geom.y if geom is not None and geom.geom_type == "Point" else None for geom in points]
            return df, None
        raise ValueError(f"Unsupported input dataset format for event extraction: {path.name}")

    def _detect_columns(self, df: pd.DataFrame) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for column in df.columns:
            canonical = _canonical_column(column)
            if canonical and canonical not in mapping:
                mapping[canonical] = str(column)
        return mapping

    def _records_from_table(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        column_map = self._detect_columns(df)
        records: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            record = {column: "" for column in OUTPUT_COLUMNS if column != "event_id"}
            for field in record:
                source_column = column_map.get(field)
                if source_column is not None and source_column in row:
                    record[field] = _clean_cell(row[source_column])
            records.append(record)
        return records

    def _llm_prompt(self, article_text: str) -> str:
        return (
            "Extract ONLY primary armed conflict or security incidents from this text and return strict JSON.\n\n"
            "Rules:\n"
            "- Extract only locations where an incident physically occurred.\n"
            "- Do not extract locations that are only mentioned for diplomacy, statements, headquarters, or background context.\n"
            "- Do not extract domestic crime, ordinary politics, court cases, accidents, or disasters unless directly tied to armed conflict.\n"
            "- Do not duplicate the same event.\n"
            "- Use real place names in city, state/province, country form when possible.\n"
            "- Include a short verbatim evidence quote from the text for each event.\n"
            "- Evidence quotes should be grounded in the input text.\n\n"
            f"Allowed categories:\n{json.dumps(CATEGORIES, indent=2)}\n\n"
            "Focus especially on these common conflict-event categories:\n"
            f"{json.dumps(FOCUS_CATEGORIES, indent=2)}\n\n"
            "Return JSON only:\n"
            "[\n"
            "  {\n"
            '    "location": "city, state/province, country",\n'
            '    "date": "YYYY-MM-DD or source date if known",\n'
            '    "category": "one allowed category",\n'
            '    "description": "short event description",\n'
            '    "evidence_quote": "verbatim quote from text"\n'
            "  }\n"
            "]\n\n"
            "If there are no valid incidents, return [].\n\n"
            f"Text:\n{article_text}"
        )

    def _extract_records_from_text(self, article_text: str, progress_callback: ProgressCallback | None) -> list[dict[str, Any]]:
        if self.client is None:
            raise ValueError(
                "LLM extraction from unstructured text requires OPENAI_API_KEY or GIBD_API_KEY. "
                "Structured event tables with clear fields can run without model credentials."
            )
        self.emit_progress(
            progress_callback,
            stage="llm_generation",
            message="Extracting conflict incidents from unstructured text with the configured model.",
            data={"model": self.model},
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You extract GIS-ready armed conflict event records and return strict JSON only.",
                },
                {"role": "user", "content": self._llm_prompt(article_text)},
            ],
            temperature=0,
        )
        self.increment_llm_calls()
        usage = getattr(response, "usage", None)
        self.input_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.output_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        content = response.choices[0].message.content if response and response.choices else ""
        return self._parse_llm_json(content)

    def _normalize_record(
        self,
        record: dict[str, Any],
        *,
        article_text: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        warnings: list[str] = []
        normalized = {column: "" for column in OUTPUT_COLUMNS}
        normalized["location"] = _normalize_location(str(record.get("location") or ""))
        normalized["date"] = _normalize_date(record.get("date")) or ""
        category, valid_category = _normalize_category(str(record.get("category") or ""))
        normalized["category"] = category
        normalized["description"] = _normalize_spaces(str(record.get("description") or ""))
        normalized["evidence_quote"] = _normalize_spaces(str(record.get("evidence_quote") or ""))
        normalized["source_name"] = _normalize_spaces(str(record.get("source_name") or record.get("source") or ""))
        normalized["source_url"] = _normalize_spaces(str(record.get("source_url") or record.get("url") or ""))

        lat, lon = _valid_coordinates(record.get("latitude"), record.get("longitude"))
        if lat is not None and lon is not None:
            normalized["latitude"] = lat
            normalized["longitude"] = lon
            normalized["geocode_status"] = "provided"
        else:
            normalized["geocode_status"] = "missing"

        validation_status = "valid"
        if _is_garbage_location(normalized["location"]):
            validation_status = "invalid"
            warnings.append("A record was missing a usable location.")
        if not valid_category:
            validation_status = "invalid"
            warnings.append(f"Category could not be mapped to an allowed value: {category}")
        if not normalized["date"]:
            validation_status = "partial" if validation_status == "valid" else validation_status
            warnings.append(f"Record at {normalized['location'] or 'unknown location'} is missing a usable date.")
        if normalized["geocode_status"] == "missing":
            validation_status = "partial" if validation_status == "valid" else validation_status
        if article_text and normalized["evidence_quote"] and not _is_grounded_quote(article_text, normalized["evidence_quote"]):
            validation_status = "partial" if validation_status == "valid" else validation_status
            warnings.append(f"Evidence quote for {normalized['location']} was not found verbatim in the source text.")
        normalized["validation_status"] = validation_status
        return normalized, warnings

    def _deduplicate_events(self, events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        seen: set[tuple[str, str, str]] = set()
        unique: list[dict[str, Any]] = []
        duplicates = 0
        for event in events:
            key = (
                _location_key(event.get("location", "")),
                str(event.get("date") or ""),
                str(event.get("category") or "").lower(),
            )
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            unique.append(event)
        for index, event in enumerate(unique, start=1):
            event["event_id"] = f"event_{index}"
        return unique, duplicates

    def _opencage_key(self) -> str | None:
        params = self.request_parameters or {}
        for key in ("OPENCAGE_API_KEY", "OPEN_CAGE_API_KEY", "opencage_api_key", "openCageApiKey"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        credential_sources = [
            params.get("source_credentials"),
            params.get("agent_specific_credentials"),
            params.get("credentials"),
        ]
        for source in credential_sources:
            if not isinstance(source, dict):
                continue
            for source_name in ("OPENCAGE", "OpenCage", "opencage"):
                entry = source.get(source_name)
                if isinstance(entry, dict):
                    for key in ("key", "api_key", "OPENCAGE_API_KEY"):
                        value = entry.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()
                elif isinstance(entry, str) and entry.strip():
                    return entry.strip()
        return os.getenv("OPENCAGE_API_KEY") or None

    def _geocode_events(self, events: list[dict[str, Any]], warnings: list[str]) -> int:
        missing = [event for event in events if event.get("geocode_status") == "missing"]
        if not missing:
            return 0
        api_key = self._opencage_key()
        if not api_key:
            warnings.append(
                "OpenCage credentials were not supplied, so records without coordinates were kept in CSV reports but omitted from GeoJSON points."
            )
            return 0

        geocoded_count = 0
        cache: dict[str, tuple[float | None, float | None, str]] = {}
        for event in missing:
            location = _clean_location_for_geocoding(str(event.get("location") or ""))
            if not location:
                event["geocode_status"] = "failed"
                continue
            if location in cache:
                lat, lon, status = cache[location]
            else:
                lat, lon, status = self._geocode_location(location, api_key, warnings)
                cache[location] = (lat, lon, status)
            if lat is not None and lon is not None:
                event["latitude"] = lat
                event["longitude"] = lon
                event["geocode_status"] = "geocoded"
                if event.get("validation_status") == "partial":
                    event["validation_status"] = "valid"
                geocoded_count += 1
            else:
                event["geocode_status"] = status
        return geocoded_count

    def _geocode_location(self, location: str, api_key: str, warnings: list[str]) -> tuple[float | None, float | None, str]:
        try:
            response = requests.get(
                "https://api.opencagedata.com/geocode/v1/json",
                params={"q": location, "key": api_key, "limit": 1, "no_annotations": 1},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("results") if isinstance(payload, dict) else None
            if results:
                geometry = results[0].get("geometry", {})
                lat, lon = _valid_coordinates(geometry.get("lat"), geometry.get("lng"))
                if lat is not None and lon is not None:
                    return lat, lon, "geocoded"
            return None, None, "failed"
        except Exception as exc:
            warnings.append(f"OpenCage geocoding failed for {location}: {exc}")
            return None, None, "failed"

    def _write_csv(self, events: list[dict[str, Any]], output_dir: Path, filename: str = "conflict_events.csv") -> str:
        path = output_dir / filename
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()
            for event in events:
                writer.writerow({column: event.get(column, "") for column in OUTPUT_COLUMNS})
        return str(path)

    def _geojson_features(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        features: list[dict[str, Any]] = []
        for event in events:
            lat, lon = _valid_coordinates(event.get("latitude"), event.get("longitude"))
            if lat is None or lon is None:
                continue
            properties = {
                key: event.get(key, "")
                for key in OUTPUT_COLUMNS
                if key not in {"latitude", "longitude"}
            }
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": properties,
                }
            )
        return features

    def _write_geojson(self, events: list[dict[str, Any]], output_dir: Path) -> tuple[str, list[dict[str, Any]]]:
        features = self._geojson_features(events)
        path = output_dir / "conflict_events.geojson"
        path.write_text(
            json.dumps({"type": "FeatureCollection", "features": features}, indent=2),
            encoding="utf-8",
        )
        return str(path), features

    def _report_data(
        self,
        *,
        input_mode: str,
        input_count: int,
        extracted_count: int,
        events: list[dict[str, Any]],
        features: list[dict[str, Any]],
        duplicate_count: int,
        geocoded_count: int,
        warnings: list[str],
    ) -> dict[str, Any]:
        missing_coordinate_count = len(events) - len(features)
        categories = sorted({str(event.get("category")) for event in events if event.get("category")})
        locations = sorted({str(event.get("location")) for event in events if event.get("location")})
        countries = sorted(
            {
                location.rsplit(",", 1)[-1].strip()
                for location in locations
                if "," in location and location.rsplit(",", 1)[-1].strip()
            }
        )
        return {
            "input_mode": input_mode,
            "input_count": input_count,
            "extracted_event_count": extracted_count,
            "clean_event_count": len(events),
            "valid_geocoded_event_count": len(features),
            "records_missing_coordinates": missing_coordinate_count,
            "duplicate_records_removed": duplicate_count,
            "newly_geocoded_records": geocoded_count,
            "categories_found": categories,
            "locations_found": locations,
            "countries_found": countries,
            "warnings": warnings,
        }

    def _write_txt_report(self, report: dict[str, Any], output_dir: Path) -> str:
        path = output_dir / "summary_report.txt"
        category_lines = [f"- {category}" for category in report["categories_found"]] or ["- None"]
        place_lines = [
            f"- {place}"
            for place in (report["countries_found"] or report["locations_found"])
        ] or ["- None"]
        warning_lines = [f"- {warning}" for warning in report["warnings"]] or ["- None"]
        lines = [
            "Spatiotemporal Conflict Event Layer Agent Summary",
            "",
            f"Input mode: {report['input_mode']}",
            f"Number of input records/articles: {report['input_count']}",
            f"Number of extracted events: {report['extracted_event_count']}",
            f"Number of clean events after deduplication: {report['clean_event_count']}",
            f"Number of valid geocoded events: {report['valid_geocoded_event_count']}",
            f"Number of records missing coordinates: {report['records_missing_coordinates']}",
            f"Number of duplicate records removed: {report['duplicate_records_removed']}",
            f"Number of newly geocoded records: {report['newly_geocoded_records']}",
            "",
            "Categories found:",
            *category_lines,
            "",
            "Countries/locations found:",
            *place_lines,
            "",
            "Warnings and credential limitations:",
            *warning_lines,
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def _write_html_report(self, report: dict[str, Any], events: list[dict[str, Any]], output_dir: Path) -> str:
        path = output_dir / "summary_report.html"
        rows = []
        for event in events:
            rows.append(
                "<tr>"
                + "".join(
                    f"<td>{html.escape(str(event.get(column, '')))}</td>"
                    for column in OUTPUT_COLUMNS
                )
                + "</tr>"
            )
        warnings = report["warnings"] or ["None"]
        body = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Spatiotemporal Conflict Event Layer Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #222; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ddd; padding: 6px; vertical-align: top; }}
    th {{ background: #f4f6f8; text-align: left; }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; margin: 16px 0; }}
    .metric {{ border: 1px solid #ddd; padding: 10px; border-radius: 6px; background: #fafafa; }}
  </style>
</head>
<body>
  <h1>Spatiotemporal Conflict Event Layer Report</h1>
  <div class="metric-grid">
    <div class="metric"><strong>Input mode</strong><br>{html.escape(str(report['input_mode']))}</div>
    <div class="metric"><strong>Inputs</strong><br>{report['input_count']}</div>
    <div class="metric"><strong>Events</strong><br>{report['clean_event_count']}</div>
    <div class="metric"><strong>GeoJSON points</strong><br>{report['valid_geocoded_event_count']}</div>
    <div class="metric"><strong>Missing coordinates</strong><br>{report['records_missing_coordinates']}</div>
    <div class="metric"><strong>Duplicates removed</strong><br>{report['duplicate_records_removed']}</div>
  </div>
  <h2>Warnings and Credential Limitations</h2>
  <ul>{''.join(f'<li>{html.escape(str(warning))}</li>' for warning in warnings)}</ul>
  <h2>Events</h2>
  <table>
    <thead><tr>{''.join(f'<th>{html.escape(column)}</th>' for column in OUTPUT_COLUMNS)}</tr></thead>
    <tbody>{''.join(rows) if rows else '<tr><td colspan="12">No events extracted.</td></tr>'}</tbody>
  </table>
</body>
</html>
"""
        path.write_text(body, encoding="utf-8")
        return str(path)

    def _write_html_map(self, events: list[dict[str, Any]], output_dir: Path, warnings: list[str]) -> str | None:
        points = []
        for event in events:
            lat, lon = _valid_coordinates(event.get("latitude"), event.get("longitude"))
            if lat is not None and lon is not None:
                points.append((event, lat, lon))
        if not points:
            return None
        path = output_dir / "conflict_event_map.html"
        try:
            import folium

            center_lat = sum(point[1] for point in points) / len(points)
            center_lon = sum(point[2] for point in points) / len(points)
            fmap = folium.Map(location=[center_lat, center_lon], zoom_start=5, tiles="OpenStreetMap")
            for event, lat, lon in points:
                popup_html = (
                    f"<strong>{html.escape(str(event.get('category', 'Event')))}</strong><br>"
                    f"{html.escape(str(event.get('location', '')))}<br>"
                    f"{html.escape(str(event.get('date', '')))}<br>"
                    f"{html.escape(str(event.get('description', '')))}"
                )
                folium.Marker(
                    location=[lat, lon],
                    popup=folium.Popup(popup_html, max_width=360),
                    tooltip=str(event.get("location") or "Conflict event"),
                ).add_to(fmap)
            fmap.save(path)
            return str(path)
        except Exception as exc:
            warnings.append(f"Folium map generation failed: {exc}")
            return None

    def run(
        self,
        query: str,
        input_dataset_paths: list[str] | str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        start_time = time.time()
        self.reset_metrics()
        dataset_paths = self.normalize_dataset_paths(input_dataset_paths)
        warnings: list[str] = []
        raw_records: list[dict[str, Any]] = []
        article_text = ""
        input_mode = "unstructured_text"
        input_count = 0

        self.emit_progress(
            progress_callback,
            stage="start",
            message="Preparing to convert conflict text or event tables into GIS-ready layers.",
            data={"dataset_count": len(dataset_paths)},
        )
        self.emit_progress(
            progress_callback,
            stage="input_inspection",
            message="Inspecting task instructions and input datasets.",
            data={"dataset_paths": dataset_paths},
        )

        dataframes: list[pd.DataFrame] = []
        text_inputs: list[str] = []
        for dataset_path in dataset_paths:
            frame, text = self._read_input_dataset(dataset_path)
            if frame is not None:
                dataframes.append(frame)
            if text:
                text_inputs.append(text)

        if dataframes:
            input_mode = "structured_table"
            combined = pd.concat(dataframes, ignore_index=True) if len(dataframes) > 1 else dataframes[0]
            input_count = int(len(combined))
            raw_records = self._records_from_table(combined)
            self.increment_tool_calls()
            self.emit_progress(
                progress_callback,
                stage="normalization",
                message="Normalizing structured event table fields into the conflict event schema.",
                data={"input_record_count": input_count},
            )
        else:
            article_text = "\n\n".join(text_inputs).strip() or self._article_text_from_query(query)
            input_count = 1 if article_text else 0
            try:
                raw_records = self._extract_records_from_text(article_text, progress_callback)
            except ValueError as exc:
                warnings.append(str(exc))
                raw_records = []

        self.emit_progress(
            progress_callback,
            stage="data_validation",
            message="Validating dates, locations, categories, evidence, and coordinates.",
            data={"raw_record_count": len(raw_records)},
        )
        normalized_events: list[dict[str, Any]] = []
        for record in raw_records:
            normalized, record_warnings = self._normalize_record(record, article_text=article_text if input_mode == "unstructured_text" else None)
            warnings.extend(record_warnings)
            normalized_events.append(normalized)

        deduped_events, duplicate_count = self._deduplicate_events(normalized_events)
        if duplicate_count:
            warnings.append(f"{duplicate_count} duplicate record(s) were removed by location/date/category key.")

        geocoded_count = self._geocode_events(deduped_events, warnings)

        output_dir = self._artifact_run_dir(query)
        self.emit_progress(
            progress_callback,
            stage="artifact_generation",
            message="Writing CSV, GeoJSON, reports, and optional HTML map artifacts.",
            data={"event_count": len(deduped_events)},
        )
        csv_file = self._write_csv(deduped_events, output_dir, "conflict_events.csv")
        geojson_file, features = self._write_geojson(deduped_events, output_dir)
        invalid_events = [event for event in deduped_events if event.get("validation_status") != "valid"]
        invalid_file = self._write_csv(invalid_events, output_dir, "invalid_records.csv") if invalid_events else None
        report = self._report_data(
            input_mode=input_mode,
            input_count=input_count,
            extracted_count=len(raw_records),
            events=deduped_events,
            features=features,
            duplicate_count=duplicate_count,
            geocoded_count=geocoded_count,
            warnings=warnings,
        )
        txt_report_file = self._write_txt_report(report, output_dir)
        html_report_file = self._write_html_report(report, deduped_events, output_dir)
        map_file = self._write_html_map(deduped_events, output_dir, warnings)

        artifact_files = [csv_file, geojson_file, txt_report_file, html_report_file]
        if map_file:
            artifact_files.append(map_file)
        if invalid_file:
            artifact_files.append(invalid_file)

        self.emit_progress(
            progress_callback,
            stage="complete",
            message="Conflict event layer generation is complete.",
            data={"artifact_count": len(artifact_files), "geojson_feature_count": len(features)},
        )

        summary = (
            f"Created a GIS-ready conflict event layer with {len(deduped_events)} event record(s), "
            f"{len(features)} GeoJSON point feature(s), and {duplicate_count} duplicate record(s) removed."
        )
        if warnings:
            summary += f" Warnings: {'; '.join(dict.fromkeys(warnings))}"

        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model,
            "duration": round(time.time() - start_time, 2),
            "total_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "inputs": {
                "text": query,
                "dataset_paths": dataset_paths,
                "parameters": {
                    "input_mode": input_mode,
                    "opencage_supplied": bool(self._opencage_key()),
                },
            },
            "outputs": {
                "text": summary,
                "csv_output_file": csv_file,
                "geojson_output_file": geojson_file,
                "text_report_file": txt_report_file,
                "html_report_file": html_report_file,
                "map_html_file": map_file,
                "invalid_records_file": invalid_file,
                "dataset_path": geojson_file,
                "event_summary": report,
            },
            "metrics": self.metrics(
                tool_calls=getattr(self, "tool_calls", 0),
                number_of_artifacts=len(artifact_files),
                duplicate_records_removed=duplicate_count,
                extracted_event_count=len(raw_records),
                geojson_feature_count=len(features),
            ),
            "environment": {
                "python_version": platform.python_version(),
                "domain-specific libraries": ["pandas", "requests", "folium"],
            },
            "stochasticity": {
                "used": self.llm_calls > 0,
                "controls": ["temperature=0"] if self.llm_calls > 0 else [],
            },
            "reproducibility_notes": [
                "Structured table normalization, validation, deduplication, CSV, GeoJSON, and report generation are deterministic.",
                "Raw unstructured text extraction uses an LLM only when OPENAI_API_KEY or GIBD_API_KEY is supplied.",
                "OpenCage geocoding is optional and only runs when credentials are supplied.",
            ],
            "complementary": {
                "Execution": {
                    "Inputs": {
                        "task": query,
                        "dataset_paths": dataset_paths,
                        "input_mode": input_mode,
                    },
                    "Outputs": {
                        "summary": summary,
                        "csv_output_file": csv_file,
                        "geojson_output_file": geojson_file,
                        "text_report_file": txt_report_file,
                        "html_report_file": html_report_file,
                        "map_html_file": map_file,
                    },
                },
                "Provenance": {
                    "Lineage": [
                        "Inspected task instructions and materialized input datasets.",
                        "Read structured event records or extracted incidents from article text.",
                        "Normalized event schema, dates, locations, categories, and coordinates.",
                        "Deduplicated records by normalized location, date, and category.",
                        "Optionally geocoded missing coordinates when OpenCage credentials were available.",
                        "Generated CSV, GeoJSON, TXT report, HTML report, and optional Folium map artifacts.",
                    ],
                    "Tool Calls": {"count": getattr(self, "tool_calls", 0)},
                    "LLM Calls": {"count": self.llm_calls},
                },
                "Validation": {
                    "status": "passed" if not any(event.get("validation_status") == "invalid" for event in deduped_events) else "warning",
                    "checks": [
                        {
                            "name": "schema_normalization",
                            "status": "passed",
                            "message": "Records were normalized to the conflict event output schema.",
                        },
                        {
                            "name": "coordinate_readiness",
                            "status": "passed" if len(features) == len(deduped_events) else "warning",
                            "message": f"{len(features)} of {len(deduped_events)} records have valid point coordinates.",
                        },
                        {
                            "name": "deduplication",
                            "status": "passed",
                            "message": f"{duplicate_count} duplicate record(s) removed.",
                        },
                    ],
                },
                "Assumptions and Limitations": {
                    "assumptions": [
                        "Structured input columns use recognizable names or aliases for event fields.",
                        "Locations and dates supplied by clients or models are source assertions and may require human review.",
                    ],
                    "limitations": [
                        "The agent does not run a continuous monitor, RSS poller, SQLite cache, Neo4j lookup, or QGIS renderer.",
                        "OpenCage geocoding is best-effort and depends on supplied credentials and network availability.",
                        "LLM text extraction may miss incidents or include false positives and should be reviewed for high-stakes use.",
                    ],
                },
            },
        }
