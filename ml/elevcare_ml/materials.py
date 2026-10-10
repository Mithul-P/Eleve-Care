"""
Material specification per component (spec section 2).

* FIELDS       - the eight fields every component form asks for, with units.
* PRESETS      - industry-typical materials per component type. Choosing one
                 auto-fills grade / composition / strength / coating.
* resolve()    - turns a stored `material_spec` into the physics parameters the
                 degradation laws need (rope breaking load, D/d, sheave
                 hardness, bearing rating C, machine thermal limit). Every
                 value is tagged `user` or `default` so the model card can show
                 where it came from. Blank fields fall back to the constants in
                 physics.py.
"""

from __future__ import annotations

import re
from dataclasses import asdict

from . import physics as P

FIELDS = [
    {"key": "material_type", "label": "Material type", "unit": ""},
    {"key": "material_grade", "label": "Material grade", "unit": ""},
    {"key": "gsm", "label": "GSM", "unit": "g/m²"},
    {"key": "thickness_mm", "label": "Thickness", "unit": "mm"},
    {"key": "composition", "label": "Composition", "unit": ""},
    {"key": "weight", "label": "Weight", "unit": "kg (kg/m for rope)"},
    {"key": "strength", "label": "Strength", "unit": "MPa / kN"},
    {"key": "coating", "label": "Coating", "unit": ""},
]
FIELD_KEYS = [f["key"] for f in FIELDS]

# Fields with real physical meaning beyond the 8 common ones (per component type).
EXTRA_FIELDS = {
    "rope": [
        {"key": "rope_count", "label": "Number of ropes in the set", "unit": "", "default": P.ROPES_PER_SET, "min": 4, "max": 8},
        {"key": "rope_diameter_mm", "label": "Rope diameter (or belt width x thickness)", "unit": "mm", "default": 10.0},
        {"key": "breaking_load_kn", "label": "Minimum breaking load per rope", "unit": "kN", "default": P.ROPE_BREAKING_LOAD_KN},
        {"key": "tensile_grade", "label": "Wire tensile grade (EN 12385-5)", "unit": "N/mm²", "default": "1570/1770"},
    ],
    "sheave": [
        {"key": "groove_type", "label": "Groove type", "unit": "", "default": "Undercut U", "options": ["U", "Undercut U", "V"]},
        {"key": "sheave_diameter_mm", "label": "Sheave diameter", "unit": "mm", "default": 400.0},
        {"key": "groove_diameter_mm", "label": "Groove diameter", "unit": "mm", "default": 10.5},
        {"key": "hardness_hb", "label": "Hardness", "unit": "HB", "default": round(P.SHEAVE_HARDNESS_PA / 9.807e6)},
    ],
    "bearing": [
        {"key": "dynamic_rating_c_kn", "label": "Dynamic load rating C", "unit": "kN", "default": P.BEARING_C_KN},
        {"key": "static_rating_c0_kn", "label": "Static load rating C0", "unit": "kN", "default": 420.0},
        {"key": "cage_material", "label": "Cage", "unit": "", "default": "Brass", "options": ["Brass", "Polyamide", "Steel"]},
        {"key": "lubrication", "label": "Lubrication", "unit": "", "default": "Lithium-complex grease"},
    ],
    "traction": [
        {"key": "insulation_class", "label": "Winding insulation class", "unit": "", "default": "F", "options": ["B", "F", "H"]},
    ],
}

# GSM only makes sense for belts, liners and linings.
GSM_APPLIES = re.compile(r"belt|liner|lining|polyurethane|PU", re.I)

PRESETS: dict[str, list[dict]] = {
    "rope": [
        {"id": "8x19S-FC", "material_type": "Steel wire rope 8x19 Seale + fibre core", "material_grade": "1570/1770 N/mm² (EN 12385-5)", "tensile_grade": "1570/1770",
         "composition": "High-carbon steel wire (0.6-0.9 % C), natural/synthetic fibre core", "strength": "MBL 60 kN per rope (10 mm)",
         "coating": "Bright, lubricated (paraffin/wax)", "thickness_mm": 10, "weight": "0.38 kg/m", "breaking_load_kn": 60.0, "rope_diameter_mm": 10.0},
        {"id": "6x19S-FC", "material_type": "Steel wire rope 6x19 Seale + fibre core", "material_grade": "1370/1770 N/mm² (EN 12385-5)", "tensile_grade": "1370/1770",
         "composition": "High-carbon steel wire (0.6-0.9 % C), fibre core", "strength": "MBL 55 kN per rope (10 mm)",
         "coating": "Bright, lubricated (paraffin/wax)", "thickness_mm": 10, "weight": "0.37 kg/m", "breaking_load_kn": 55.0, "rope_diameter_mm": 10.0},
        {"id": "8x19S-IWRC", "material_type": "Steel wire rope 8x19 Seale + steel core (IWRC)", "material_grade": "1770/1960 N/mm² (EN 12385-5)", "tensile_grade": "1770/1960",
         "composition": "High-carbon steel wire (0.6-0.9 % C), independent wire-rope core", "strength": "MBL 72 kN per rope (10 mm)",
         "coating": "Zinc-galvanised, lubricated", "thickness_mm": 10, "weight": "0.44 kg/m", "breaking_load_kn": 72.0, "rope_diameter_mm": 10.0},
        {"id": "PU-belt", "material_type": "PU-coated flat steel belt", "material_grade": "Steel cord 1770 N/mm² in PU jacket", "tensile_grade": "1770",
         "composition": "Zinc-coated steel cords in polyurethane", "strength": "MBL 90 kN per belt", "coating": "Polyurethane jacket",
         "thickness_mm": 3, "weight": "0.30 kg/m", "gsm": 2400, "breaking_load_kn": 90.0, "rope_diameter_mm": 3.0},
        {"id": "CF-rope", "material_type": "Carbon-fibre core rope with friction coating", "material_grade": "CFRP core, high-friction jacket", "tensile_grade": "n/a (CFRP)",
         "composition": "Carbon-fibre core, polymer friction coating", "strength": "MBL 80 kN per rope", "coating": "High-friction polymer",
         "thickness_mm": 8, "weight": "0.12 kg/m", "gsm": 1800, "breaking_load_kn": 80.0, "rope_diameter_mm": 8.0},
    ],
    "sheave": [
        {"id": "GJL-250", "material_type": "Grey cast iron EN-GJL-250 (GG25)", "material_grade": "EN-GJL-250", "composition": "Grey cast iron, flake graphite",
         "strength": "250 MPa tensile, ~220 HB", "coating": "None", "thickness_mm": 60, "weight": "185 kg", "hardness_hb": 220},
        {"id": "GJS-500-7", "material_type": "Ductile iron EN-GJS-500-7", "material_grade": "EN-GJS-500-7", "composition": "Spheroidal-graphite iron",
         "strength": "500 MPa tensile, ~190 HB", "coating": "None", "thickness_mm": 55, "weight": "180 kg", "hardness_hb": 190},
        {"id": "GJS-600-3", "material_type": "Ductile iron EN-GJS-600-3", "material_grade": "EN-GJS-600-3", "composition": "Spheroidal-graphite iron, pearlitic",
         "strength": "600 MPa tensile, ~230 HB", "coating": "Induction-hardened grooves", "thickness_mm": 55, "weight": "180 kg", "hardness_hb": 230},
        {"id": "C45", "material_type": "Forged steel C45", "material_grade": "C45 (1.0503)", "composition": "Medium-carbon steel, 0.45 % C",
         "strength": "600 MPa tensile, ~210 HB (hardened grooves 50 HRC)", "coating": "Induction-hardened grooves", "thickness_mm": 50, "weight": "165 kg", "hardness_hb": 210},
        {"id": "PU-liner", "material_type": "PU groove liner on cast iron sheave", "material_grade": "Polyurethane 92 Shore A",
         "composition": "Cast polyurethane liner, cast-iron body", "strength": "35 MPa tensile (liner)", "coating": "PU liner",
         "thickness_mm": 12, "weight": "190 kg", "gsm": 3200, "hardness_hb": 25},
    ],
    "bearing": [
        {"id": "SRB", "material_type": "Spherical roller bearing", "material_grade": "100Cr6 / SAE 52100 / GCr15, 58-65 HRC",
         "composition": "Chromium bearing steel rings and rollers, brass cage", "strength": "C = 300 kN, C0 = 420 kN", "coating": "Lithium-complex grease, seals",
         "thickness_mm": 22, "weight": "9.5 kg", "dynamic_rating_c_kn": 300.0, "static_rating_c0_kn": 420.0, "cage_material": "Brass"},
        {"id": "DGBB", "material_type": "Deep-groove ball bearing", "material_grade": "100Cr6 / SAE 52100, 58-65 HRC",
         "composition": "Chromium bearing steel, polyamide cage", "strength": "C = 160 kN, C0 = 190 kN", "coating": "Grease, shields",
         "thickness_mm": 18, "weight": "5.8 kg", "dynamic_rating_c_kn": 160.0, "static_rating_c0_kn": 190.0, "cage_material": "Polyamide"},
        {"id": "TRB", "material_type": "Tapered roller bearing", "material_grade": "100Cr6 / GCr15, 58-65 HRC",
         "composition": "Chromium bearing steel, steel cage", "strength": "C = 380 kN, C0 = 560 kN", "coating": "Black oxide, grease",
         "thickness_mm": 24, "weight": "11 kg", "dynamic_rating_c_kn": 380.0, "static_rating_c0_kn": 560.0, "cage_material": "Steel"},
    ],
    # traction machine: one preset per repeatable part row
    "traction": [
        {"id": "laminations", "part": "Stator / rotor laminations", "material_type": "Silicon steel", "material_grade": "M400-50A",
         "composition": "Fe + 2.5-3 % Si, 0.5 mm laminations", "strength": "350 MPa yield", "coating": "Insulating varnish", "thickness_mm": 0.5, "weight": "120 kg"},
        {"id": "windings", "material_type": "Enamelled copper", "part": "Windings", "material_grade": "Insulation class F",
         "composition": "Electrolytic copper, polyester-imide enamel", "strength": "Class F: 155 °C", "coating": "VPI epoxy varnish", "thickness_mm": 1.2, "weight": "45 kg", "insulation_class": "F"},
        {"id": "magnets", "part": "Magnets (gearless PM)", "material_type": "NdFeB permanent magnets", "material_grade": "N38SH",
         "composition": "Nd-Fe-B sintered, 1.22 T remanence", "strength": "Max 150 °C operating", "coating": "Nickel (Ni-Cu-Ni)", "thickness_mm": 6, "weight": "8 kg", "drive": "gearless"},
        {"id": "shaft", "part": "Shaft", "material_type": "Alloy steel", "material_grade": "42CrMo4 (1.7225)",
         "composition": "Cr-Mo quenched and tempered steel", "strength": "900 MPa tensile", "coating": "None", "thickness_mm": 90, "weight": "38 kg"},
        {"id": "shaft-c45", "part": "Shaft", "material_type": "Carbon steel", "material_grade": "C45",
         "composition": "Medium-carbon steel", "strength": "600 MPa tensile", "coating": "None", "thickness_mm": 90, "weight": "38 kg"},
        {"id": "brake", "part": "Brake lining", "material_type": "Asbestos-free friction material", "material_grade": "Organic/semi-metallic, μ = 0.4",
         "composition": "Aramid fibre, resin binder, metallic fillers", "strength": "Max 300 °C", "coating": "None", "thickness_mm": 8, "weight": "1.2 kg", "gsm": 4200},
        {"id": "worm", "part": "Worm gear (geared drive)", "material_type": "Phosphor-bronze wheel + hardened steel worm", "material_grade": "CuSn12 wheel / 16MnCr5 worm",
         "composition": "Bronze wheel rim, case-hardened steel worm", "strength": "270 MPa (bronze), 58 HRC (worm)", "coating": "None", "thickness_mm": 25, "weight": "42 kg", "drive": "geared"},
    ],
}

INSULATION_CLASS_C = {"A": 105, "B": 130, "F": 155, "H": 180}
CLASS_F_ALLOWED_RISE_K = 105.0  # class F: 155 °C limit over a 40 °C reference ambient (+ hotspot allowance)


def gsm_applicable(component_type: str, material_type: str | None) -> bool:
    if component_type == "bearing":
        return False
    return bool(material_type and GSM_APPLIES.search(material_type))


def apply_preset(component_type: str, preset_id: str) -> dict:
    """The auto-fill values for a preset (user may edit any of them)."""
    for p in PRESETS.get(component_type, []):
        if p["id"] == preset_id:
            out = {k: v for k, v in p.items() if k != "id"}
            out["preset"] = preset_id
            if "gsm" not in out:
                out["gsm"] = None if gsm_applicable(component_type, p["material_type"]) else "Not applicable"
            return out
    raise KeyError(preset_id)


def _num(v, default=None):
    try:
        if v in (None, "", "Not applicable"):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _class_from_rows(spec: dict):
    for row in spec.get("rows", []) or []:
        c = (row.get("insulation_class") or "").strip().upper()
        if c in INSULATION_CLASS_C:
            return c
        m = re.search(r"class\s*([ABFH])", str(row.get("material_grade", "")) + " " + str(row.get("strength", "")), re.I)
        if m:
            return m.group(1).upper()
    c = (spec.get("insulation_class") or "").strip().upper()
    return c if c in INSULATION_CLASS_C else None


def resolve(component_type: str, spec: dict | None) -> dict:
    """
    Physics inputs for a component with provenance. Returns
    {"params": {...}, "inputs": [{key, label, value, unit, source}], "completeness_pct": int}
    """
    spec = spec or {}
    inputs: list[dict] = []
    params: dict = {}

    def put(key, label, unit, user_val, default):
        src = "user" if user_val is not None else "default"
        val = user_val if user_val is not None else default
        params[key] = val
        inputs.append({"key": key, "label": label, "unit": unit, "value": val, "source": src, "default": default})

    if component_type == "rope":
        put("rope_breaking_load_kn", "Rope minimum breaking load S_u", "kN", _num(spec.get("breaking_load_kn")), P.ROPE_BREAKING_LOAD_KN)
        d = _num(spec.get("rope_diameter_mm")) or _num(spec.get("thickness_mm"))
        sheave_d = _num(spec.get("sheave_diameter_mm"))
        dd = sheave_d / d if (d and sheave_d) else None
        put("d_over_d", "Sheave / rope diameter ratio D/d", "", dd, P.ROPE_D_OVER_D)
        put("ropes_per_set", "Ropes in the set", "", int(_num(spec.get("rope_count"))) if _num(spec.get("rope_count")) else None, P.ROPES_PER_SET)
    elif component_type == "sheave":
        hb = _num(spec.get("hardness_hb"))
        put("sheave_hardness_pa", "Sheave hardness H (HB × 9.807e6)", "Pa", hb * 9.807e6 if hb else None, P.SHEAVE_HARDNESS_PA)
        put("sheave_diameter_mm", "Sheave diameter", "mm", _num(spec.get("sheave_diameter_mm")), P.ROPE_D_OVER_D * 10.0)
    elif component_type == "bearing":
        put("bearing_c_kn", "Bearing dynamic load rating C", "kN", _num(spec.get("dynamic_rating_c_kn")), P.BEARING_C_KN)
    elif component_type == "traction":
        cls = _class_from_rows(spec)
        limit = INSULATION_CLASS_C.get(cls) if cls else None
        # Arrhenius reference: the rated winding rise scales with the class's allowed rise (class F = 15 C).
        rise = P.TRACTION_RATED_TEMP_RISE_C * ((limit - 40 - 10) / CLASS_F_ALLOWED_RISE_K) if limit else None
        put("insulation_limit_c", f"Winding thermal limit (insulation class {cls or 'F'})", "°C", limit, INSULATION_CLASS_C["F"])
        put("rated_temp_rise_c", "Rated winding temperature rise", "°C", round(rise, 2) if rise else None, P.TRACTION_RATED_TEMP_RISE_C)

    return {"params": params, "inputs": inputs, "completeness_pct": completeness(component_type, spec)}


def completeness(component_type: str, spec: dict | None) -> int:
    spec = spec or {}
    if component_type == "traction":
        rows = spec.get("rows") or []
        if not rows:
            return 0
        filled = total = 0
        for row in rows:
            for k in FIELD_KEYS:
                total += 1
                filled += 1 if row.get(k) not in (None, "") else 0
        return round(100 * filled / max(1, total))
    keys = FIELD_KEYS + [f["key"] for f in EXTRA_FIELDS.get(component_type, [])]
    filled = sum(1 for k in keys if spec.get(k) not in (None, ""))
    return round(100 * filled / len(keys))


def blank_fields(component_type: str, spec: dict | None) -> list[str]:
    """Fields left blank (shown with a 'using default value' tag)."""
    spec = spec or {}
    if component_type == "traction":
        return [] if spec.get("rows") else FIELD_KEYS
    keys = FIELD_KEYS + [f["key"] for f in EXTRA_FIELDS.get(component_type, [])]
    return [k for k in keys if spec.get(k) in (None, "")]


def schema(component_type: str) -> dict:
    return {"fields": FIELDS, "extra_fields": EXTRA_FIELDS.get(component_type, []), "presets": PRESETS.get(component_type, []),
            "repeatable": component_type == "traction",
            "gsm_rule": "GSM applies to belts, liners and linings only; auto-set to 'Not applicable' for steel wire, cast or forged parts."}


__all__ = ["FIELDS", "EXTRA_FIELDS", "PRESETS", "apply_preset", "resolve", "completeness", "blank_fields", "schema",
           "gsm_applicable", "asdict"]
