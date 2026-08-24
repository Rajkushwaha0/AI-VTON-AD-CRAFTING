# MODEL FACE IDENTITY ANALYSIS — PIXEL-LEVEL TAXONOMY (runs alongside garment analysis)

When a human model is visible in the product images, you MUST analyze the model's face with **forensic, pixel-level rigor** — the same detail as garment construction. Scan **every reference image** individually, then merge into one canonical `model_face_identity` spec. Downstream image generation treats this as the **ONLY authority** for facial identity: every documented attribute must be reproduced exactly. Do not beautify, redraw, smooth, symmetrize, or omit documented features.

The model may be male or female; document gender presentation accurately.

---

## Per-image protocol (MANDATORY)

For **each** uploaded image, add one entry to `per_image_face_analysis`:

| Field | Values / notes |
|-------|----------------|
| `image_file_name` | exact filename |
| `face_visible` | true if any facial features discernible |
| `face_visibility_quality` | `excellent` \| `good` \| `partial` \| `poor` \| `none` |
| `face_angle` | `frontal` \| `three_quarter_left` \| `three_quarter_right` \| `profile_left` \| `profile_right` \| `downward` \| `upward` \| `occluded` |
| `features_clearly_visible` | list from taxonomy sections below |
| `features_partially_visible` | same vocabulary |
| `features_not_visible` | same vocabulary |
| `image_face_observations` | object with only fields verifiable **in this image** (subset of sections below) |

---

## Canonical merge (`model_face_identity`)

Merge observations across all images. When images conflict, prefer the **clearest frontal/three-quarter** view. Populate `best_reference_images` with filenames ranked best-first (sharp, unobstructed eyes/nose/lips, minimal occlusion).

**Populate EVERY section below** with all sub-fields you can verify. Use `null` or `not_visible` only when truly absent in all images — never guess teeth if never shown.

---

### 1. Overall face geometry (`overall_geometry`)

| Field | Allowed values |
|-------|----------------|
| `face_shape` | oval, round, square, rectangular, oblong, heart, diamond, pear, triangle, inverted_triangle |
| `face_length` | very_short, short, medium, long, very_long |
| `face_width` | narrow, medium, wide |
| `width_to_height_ratio` | descriptive ratio note |
| `upper_face_proportion` | short, average, long |
| `mid_face_proportion` | short, average, long |
| `lower_face_proportion` | short, average, long |
| `facial_fullness` | slim, average, full, chubby |

### 2. Forehead (`forehead`)

| Field | Allowed values |
|-------|----------------|
| `height` | low, medium, high |
| `width` | narrow, average, broad |
| `curvature` | flat, slightly_curved, rounded |
| `hairline_shape` | straight, rounded, m_shaped, widows_peak, uneven |
| `hairline_height` | low, medium, high |
| `hairline_density` | thick, slight_recession, receding |

### 3. Hair (`hair`)

| Field | Allowed values |
|-------|----------------|
| `color` | jet_black, black, dark_brown, brown, light_brown, blonde, auburn, red, gray, white |
| `texture` | pin_straight, straight, slightly_wavy, wavy, curly, tight_curly, coily |
| `thickness` | fine, medium, thick |
| `density` | sparse, medium, dense |
| `shine` | matte, natural, glossy |
| `parting` | middle, left, right, none |
| `length` | buzz_cut, pixie, ear_length, shoulder, chest, waist |
| `baby_hairs_at_hairline` | note if visible |

### 4. Eyebrows (`eyebrows`) — strong identity feature

| Field | Allowed values |
|-------|----------------|
| `thickness` | very_thin, thin, medium, thick, very_thick |
| `density` | sparse, medium, dense |
| `arch` | flat, soft, medium, high, sharp |
| `length` | short, medium, long |
| `tail` | long, short, tapered |
| `position` | close_to_eyes, high_above_eyes |
| `symmetry` | symmetric, slight_asymmetry_left_higher, slight_asymmetry_right_higher |
| `color` | descriptive |

### 5. Eyes (`eyes`) — most recognizable feature

| Field | Allowed values |
|-------|----------------|
| `shape` | almond, round, hooded, monolid, deep_set, protruding, upturned, downturned |
| `size` | small, medium, large |
| `width` | descriptive |
| `height` | descriptive |
| `depth` | shallow, medium, deep_set |
| `rotation` | upward, neutral, downward |
| `separation` | close_set, average, wide_set |
| `iris_color` | dark_brown, brown, hazel, green, blue, gray |
| `iris_size` | descriptive |
| `pupil_size` | descriptive |
| `sclera_visibility` | minimal, average, visible |
| `eyelid_type` | monolid, double_eyelid, hooded |
| `upper_eyelid_fold` | absent, subtle, pronounced |
| `lower_eyelid_shape` | descriptive |
| `tear_duct_shape` | descriptive |
| `symmetry` | symmetric, slight_asymmetry |

### 6. Eyelashes (`eyelashes`)

| Field | Notes |
|-------|-------|
| `upper_length` | short, medium, long |
| `upper_curl` | straight, slight_curl, curled |
| `upper_density` | sparse, medium, dense |
| `upper_direction` | descriptive |
| `lower_length` | short, medium, long |
| `lower_density` | sparse, medium, dense |

### 7. Nose (`nose`) — major identity structure

| Field | Allowed values |
|-------|----------------|
| `overall_size` | small, medium, large |
| `length` | short, medium, long |
| `width` | narrow, medium, wide |
| `bridge_shape` | straight, convex, concave |
| `bridge_height` | low, medium, high |
| `tip_shape` | rounded, pointed, bulbous, upturned, downturned |
| `nostril_size` | small, medium, large |
| `nostril_shape` | oval, round, triangular |
| `nostril_visibility` | low, medium, high |
| `alar_width` | narrow, medium, wide |
| `nasal_base_width` | narrow, medium, wide |
| `nose_angle` | descriptive |
| `columella_note` | descriptive if visible |

### 8. Cheeks (`cheeks`)

| Field | Allowed values |
|-------|----------------|
| `fullness` | hollow, average, full |
| `cheekbone_height` | low, medium, high |
| `cheekbone_width` | narrow, medium, wide |
| `soft_tissue_note` | descriptive |

### 9. Lips (`lips`)

| Field | Allowed values |
|-------|----------------|
| `width` | narrow, medium, wide |
| `height` | short, medium, tall |
| `upper_lip_thickness` | thin, medium, full |
| `lower_lip_thickness` | thin, medium, full |
| `lip_ratio` | upper_dominant, balanced, lower_dominant |
| `cupids_bow` | sharp, rounded, flat |
| `vermilion_border` | sharp, soft, blended |
| `lip_color` | descriptive |
| `lip_texture` | smooth, lined, dry |
| `lip_symmetry` | symmetric, slight_asymmetry |
| `corner_position` | neutral, upturned, downturned |

### 10. Mouth (`mouth`)

| Field | Notes |
|-------|-------|
| `width` | descriptive |
| `smile_line` | absent, subtle, pronounced |
| `resting_expression` | neutral, soft_smile, parted, pursed |
| `corner_position` | neutral, upturned, downturned |
| `philtrum_note` | descriptive |

### 11. Teeth (`teeth`)

| Field | Notes |
|-------|-------|
| `visibility` | not_visible, partially_visible, visible |
| `alignment` | straight, slight_gap, crooked — only if visible |
| `gap_note` | descriptive if visible |
| `whiteness` | only if visible |
| `tooth_size` | only if visible |
| `shape` | only if visible |

### 12. Chin (`chin`)

| Field | Allowed values |
|-------|----------------|
| `shape` | pointed, rounded, square |
| `width` | narrow, medium, wide |
| `length` | short, medium, long |
| `projection` | receding, neutral, forward |

### 13. Jawline (`jawline`)

| Field | Allowed values |
|-------|----------------|
| `width` | narrow, medium, wide |
| `definition` | soft, moderate, sharp |
| `angle` | descriptive |
| `symmetry` | symmetric, slight_asymmetry |

### 14. Neck (`neck`)

| Field | Notes |
|-------|-------|
| `length` | short, medium, long |
| `thickness` | slim, average, thick |
| `jaw_neck_transition` | sharp, soft, blended |

### 15. Ears (`ears`)

| Field | Allowed values |
|-------|----------------|
| `size` | small, medium, large |
| `shape` | descriptive |
| `earlobe_type` | attached, detached |
| `position` | descriptive |
| `rotation` | descriptive |

### 16. Skin (`skin`)

| Field | Allowed values |
|-------|----------------|
| `tone` | descriptive (e.g. fair, medium, deep) |
| `undertone` | warm, cool, neutral |
| `texture` | smooth, visible_pores, acne_scars, textured |
| `reflectivity` | matte, natural, dewy, oily |
| `freckles` | none, sparse, moderate, dense + location |
| `pigmentation` | none, mild, moderate + location |
| `wrinkles` | none, mild, moderate + location |
| `fine_lines` | none, mild, moderate + location |

### 17. Facial symmetry (`facial_symmetry`)

| Field | Notes |
|-------|-------|
| `eye_alignment` | aligned, slight_offset |
| `nose_alignment` | centered, slight_offset |
| `lip_alignment` | aligned, slight_offset |
| `jaw_alignment` | aligned, slight_offset |
| `chin_alignment` | aligned, slight_offset |
| `natural_asymmetries` | **list every visible asymmetry** — do not correct |

### 18. Facial landmarks (`facial_landmarks`)

Relative positions and distances between key landmarks (jaw contour, chin tip, lip outline, nose bridge/tip, nostrils, eye corners, eyelid contours, iris centers, eyebrow contours, hairline). Document as descriptive notes:

| Field | Notes |
|-------|-------|
| `jaw_contour` | descriptive |
| `chin_tip` | descriptive |
| `lip_outline` | descriptive |
| `nose_bridge` | descriptive |
| `nose_tip` | descriptive |
| `nostrils` | descriptive |
| `eye_corners_inner_outer` | descriptive |
| `eyelid_contours` | descriptive |
| `iris_centers` | descriptive |
| `eyebrow_contours` | descriptive |
| `hairline_contour` | descriptive if visible |
| `landmark_distance_notes` | interpupillary, eye-to-nose, nose-to-lip, lip-to-chin ratios |

### 19. Facial ratios (`facial_ratios`)

Stable geometric ratios (more reliable than absolute size):

| Field | Notes |
|-------|-------|
| `interpupillary_distance_note` | descriptive |
| `eye_width_to_face_width` | descriptive |
| `nose_width_to_eye_distance` | descriptive |
| `nose_length_to_face_height` | descriptive |
| `mouth_width_to_nose_width` | descriptive |
| `forehead_height_to_face_height` | descriptive |
| `chin_length_to_lower_face_height` | descriptive |
| `jaw_width_to_cheekbone_width` | descriptive |

### 20. Baseline expression

Top-level `baseline_expression`: `neutral` \| `soft_smile` \| `broad_smile` \| `closed_mouth` \| `open_mouth`

Keep consistent across generation — only minor scene-appropriate variation allowed.

### 21. Distinguishing features (`distinguishing_features`)

Array of `{feature, location, description}` — **every** visible:
- moles, freckles, birthmarks, scars, dimples, beauty marks, piercings, wrinkles, asymmetries

Include **exact position** (e.g. "small silver septum ring", "mole 2mm below left nostril").

### 22. Age indicators (`age_indicators`)

Do not change apparent age — document:
`skin_elasticity`, `under_eye_fullness`, `nasolabial_folds`, `forehead_lines`, `crows_feet`, `facial_fat_distribution`

### 23. Makeup (`makeup`)

Object or null — if present: `foundation`, `eyeliner`, `mascara`, `eyeshadow`, `blush`, `contour`, `highlighter`, `lipstick`

### 24. Glasses and accessories (`glasses_accessories`)

Object or null — if part of identity: `frame_shape`, `frame_color`, `lens_type`, `size`, `position`

### 25. Facial hair (`facial_hair`)

Object or null (male) — `beard_style`, `mustache_style`, `sideburns`, `stubble_density`, `beard_length`, `beard_contour`

---

## Required top-level keys in `model_face_identity`

`identity_summary`, `best_reference_images`, `baseline_expression`, and **all 25 section objects** above:
`overall_geometry`, `forehead`, `hair`, `eyebrows`, `eyes`, `eyelashes`, `nose`, `cheeks`, `lips`, `mouth`, `teeth`, `chin`, `jawline`, `neck`, `ears`, `skin`, `facial_symmetry`, `facial_landmarks`, `facial_ratios`, `distinguishing_features`, `age_indicators`, `makeup`, `glasses_accessories`, `facial_hair`

---

## HARD RULES

- Only record what is **visually verifiable** in the image set.
- Do **not** invent features not visible in any image.
- Document **natural asymmetries** — never idealize to doll symmetry.
- `distinguishing_features` must list **every** visible mole, freckle cluster, scar, dimple, piercing with location.
- If no human face appears, set `model_face_identity` to `null`.
- Downstream generation will reproduce **every documented field** at pixel-level fidelity — incomplete analysis causes identity drift.
