# AdaptSG design system

The visual language of `public/index.html`. It follows the Mobbin style reference the repository
owner supplied — an achromatic specimen board where typographic weight does the work colour
usually does — adapted where AdaptSG's audience and safety rules require it.

The reference is reproduced below from **"Style reference"** onward. Read the deviations first:
they are the parts where copying the reference would have been wrong.

## Deviations from the reference, and why

**1. Safety keeps its chroma. Nothing else does.**
The reference says *"never introduce a chromatic accent colour."* Every piece of AdaptSG chrome
obeys that — buttons, nav, cards, borders, links, focus rings, map pins, the timeline spine. But
the status chips encode a safety verdict on wheelchair access, a walking limit and a budget
(`AGENTS.md` safety rules 3–5), so three chromatic tokens survive:

| Token | Value | On `--canvas` | Meaning |
|---|---|---|---|
| `--breach` | `#b42318` | 6.6:1 | a hard constraint is exceeded |
| `--caution` | `#b54708` | 5.4:1 | unverified, stale, or awaiting a decision |
| `--pass` | `#067647` | 4.9:1 | checked and within limits |

Each is always paired with an icon **and** a word, so colour is never the only channel and the
page still reads correctly in greyscale. `.track.over i` keeps `--breach` for the same reason: it
reports the walking limit being exceeded.

The mascot is the one decorative chromatic element on the page. That is the reference's own
exception — it describes the Revolut icon in its hero as *"the only chromatic element … a
deliberate specimen of the content being showcased, not a brand choice."*

**2. The type ramp is scaled up, and `--ash` never carries text.**
The reference's 14–16px body suits a gallery for designers. AdaptSG is used by elderly travellers
and their caregivers and holds `body{font-size:18px}`. The ramp is scaled roughly 1.15× rather
than adopted literally:

| Role | Reference | AdaptSG | Weight |
|---|---|---|---|
| caption | 12px | 14px | 400 |
| body-sm | 14px | 16px | 400 |
| body | 16px | **18px** | 400 |
| subheading | 20px | 22px | 456 |
| heading | 32px | 26–34px | 600 |
| view `h1` | 56px | 40px | 600, `-0.007em` |
| hero display | 80px | `clamp(40px,6vw,72px)` | 652, `-0.011em` |

`--ash` (`#adadad`) is 2.2:1 on white. It is a **borders-and-fills token only**; text that would
have used it takes `--muted` (4.9:1) or `--disabled` (4.5:1). `scripts/check_web.mjs` enforces
this — it is the rule in the system easiest to undo by accident, because `--ash` reads as "a grey"
at a glance.

**3. Provenance is achromatic but must stay unmistakable.**
`AGENTS.md` rule 11 forbids representing demo estimates as live data. The DEMO banner therefore
does not use an amber wash; it carries the claim through surface, texture and weight instead —
`--mist` surface, a hatched `--ink` edge, an uppercase 600-weight title — against LIVE's plain
surface and solid edge. The pinned badge literals in `modeBadge()` mirror
`adaptsg.presentation.mode_badge` and are compared verbatim by a gate; restyle the banner, never
retype the strings.

**4. Not everything the reference specifies exists here.**
- `saans` is not licensed. Inter is the substitute the reference names, loaded from the variable
  axis (`wght@400..700`) — **not** a list of static instances, or the browser silently snaps 440,
  456 and 652 to 400 and 600 with no error.
- The reference's `font-feature-settings:"ss07"` is a saans stylistic set with no Inter
  equivalent, so it is not copied. Inter's `"cv05"` (tailed lowercase l) is used instead, a real
  legibility gain for this audience.
- `9999px` inputs apply to the single-line date field. The prompt `textarea` takes `--r-card`; a
  multi-line pill is not what the reference is describing.
- The Tailwind v4 token block is omitted: this client has no build step and no Tailwind.

## Tokens as implemented

```css
--ink:#141414;  --canvas:#ffffff; --muted:#707070; --disabled:#767676;
--ash:#adadad;  --fog:#ededed;    --mist:#f2f2f2;  --silver:#c2c2c2;
--breach:#b42318; --caution:#b54708; --pass:#067647;
--w-body:400; --w-ui:440; --w-mid:456; --w-head:600; --w-display:652;
--r-pill:9999px; --r-lg:24px; --r-card:16px; --r-sm:8px;
--page:1280px; --section:80px;
--ring:rgba(64,64,64,.16) 0 0 0 1px inset;
```

Surfaces: page and card are both `--canvas`, separated by a 1px `--fog` border rather than a
background change. `--mist` is the inner fill for the nav pill, inputs and must-have pills.
**Cards never take a box-shadow** — `--ring` exists only for button hover.

---

# Style reference — Mobbin

> Grayscale specimen board — a printer's proof sheet where typographic weight IS color.

**Theme:** light

Mobbin runs on pure achromatic restraint — zero chroma across the entire palette, forcing
hierarchy through weight, size, and tone alone. The page is white space interrupted by near-black
ink (#141414) at display sizes and warm-gray (#707070, #adadad) for secondary text. The custom
'saans' typeface is the single differentiator: fractional weights (440, 456, 652) that don't exist
in any system font, creating headline mass that sits between regular and semibold — typography
doing the work of color. 9999px pill shapes appear on every interactive element while card content
sits on 16-24px rounded rectangles, making buttons feel like badges in a sea of contained
thumbnails.

## Tokens — Colors

| Name | Value | Token | Role |
|------|-------|-------|------|
| Midnight Ink | `#141414` | `--color-midnight-ink` | Primary text, headings, filled CTA buttons, nav items, icon strokes |
| Pure Canvas | `#ffffff` | `--color-pure-canvas` | Page background, card surfaces, button text on dark fills |
| Graphite | `#707070` | `--color-graphite` | Body copy, secondary links, descriptive text |
| Ash | `#adadad` | `--color-ash` | Tertiary text, disabled/muted button borders, placeholder icons |
| Fog | `#ededed` | `--color-fog` | Dividers, subtle borders, card outlines |
| Mist | `#f2f2f2` | `--color-mist` | Nav background tint, input fields, inner surface elevation |
| Silver | `#c2c2c2` | `--color-silver` | Skeleton loaders, inactive UI fills |
| Slate Shadow | `#e0e0e0` | `--color-slate-shadow` | Inset button shadow ring |

## Tokens — Typography

`saans`, substituted by Inter Variable or Geist. Weights 400, 440, 456, 600, 652. Sizes 12, 14,
16, 20, 32, 56, 80px. Line height 1.00–1.15 at display, 1.38–1.50 at body. Letter spacing
-0.88px at 80px, -0.39px at 56px, +0.21px at 16px, +0.28px at 20px, +0.20px at 12px.

Fractional weights map to semantic roles: 400 long-form body and footnotes; 440 UI labels, nav
links, card metadata, secondary button text; 456 mid-emphasis body, subheadings, feature
descriptions; 600 headlines, section titles, CTA text; 652 hero display numerics and
maximum-emphasis headlines at 56–80px. The gap between 440 and 456 is subtle but intentional —
456 is used where 440 reads too light on white at 16–20px but 600 would feel heavy. Never
substitute 500 or 700.

## Tokens — Spacing & Shapes

Base unit 8px, comfortable density. Scale: 8, 16, 24, 32, 40, 64, 80, 104, 120px.

Radius: tags/inputs/buttons `9999px`, cards 16–24px, modals 24px, thumbnails 16px.
Shadows: `subtle` = `rgba(64,64,64,0.16) 0 0 0 1px inset`; `xl` = `rgba(0,0,0,0.04) 0 8px 40px`.
Layout: page max-width 1280px, section gap 80px, card padding 16–24px, element gap 8–16px.

## Components

- **Primary filled button** — `#141414` background, `#ffffff` text, radius 9999px, padding 0 16px,
  weight 600 at 14–16px, inset ring on hover.
- **Outlined pill button** — transparent, `#141414` text and 1px border, same radius and padding.
- **Muted pill button** — transparent, `#adadad` text and border, padding 0 12px. Tertiary or
  inactive filter tabs.
- **Inline underline link** — no pill, no border, purely typographic, 2px padding.
- **Content card** — white, 1px `#ededed` border, radius 16px, padding 16px. No shadow.
- **Overlay dropdown** — white, radius 24px, padding 24–32px, `rgba(0,0,0,0.04) 0 8px 40px`. The
  only place a shadow is used.
- **Top navigation bar** — `#ffffff` or `#f2f2f2` tint, height 60px, padding 0 24px. Logo left,
  links (14px weight 440) centre, filled CTA pill right. No underlines, no borders on links.
- **Filter pill row** — active: `#141414` fill, white text. Inactive: transparent, `#141414`
  border and text. Radius 9999px, padding 4px 12px, weight 440, 8px gap.
- **Search input** — `#f2f2f2`, no border, radius 9999px, padding 8px 16px.
- **Section stat display** — the number at 56–80px weight 652, tracking -0.88px, line-height 1.00;
  label below at 16–20px weight 440 `#707070`. The numbers are the centrepiece.

## Do

- Use `#141414` as the only "color" — every accent, icon, filled button and active state.
- Apply 9999px radius to every interactive pill; 16–24px to non-interactive containers.
- Set display headlines (56–80px) at weight 600–652, tracking -0.007em to -0.011em.
- Differentiate card elevation with 1px `#ededed` borders only. Reserve box-shadow for floating
  dropdowns.
- Use the fractional weights: 440 for UI labels and nav, 456 for mid-emphasis body, 652 for hero
  numerics. Never round to 400/500/600 where the fractional weight is available.
- Maintain an 80px vertical rhythm between major blocks, 24px internal card padding as baseline.

## Don't

- Never introduce a chromatic accent colour — not blue for links, not green for success.
- Never use box-shadow on cards or thumbnails; borders do that work.
- Never use font-weight 700 or 800. The heaviest weight is 652.
- Never use radius values outside 9999px (interactive), 24px (large containers), 16px
  (cards/images) or 8px (inline badges).
- Never place a coloured background behind a section — `#ffffff` vs `#f2f2f2` at most.
- Never left-align hero headlines; the centred display type is the layout anchor.
- Never remove letter-spacing from display type.

## Surfaces and elevation

| Level | Name | Value | Purpose |
|---|---|---|---|
| 0 | Page | `#ffffff` | Root background |
| 1 | Card | `#ffffff` | Differentiated by a 1px `#ededed` border, not a background change |
| 2 | Input / chip | `#f2f2f2` | Search bars, nav tint, inner fills |
| 3 | Overlay dropdown | `#ffffff` | Elevated by `rgba(0,0,0,0.04) 0 8px 40px` |

Shadows are nearly absent. Card elevation is expressed entirely through borders against a white
page, which keeps the visual field clean enough that content reads as the highest-contrast element
on every surface.

## Layout

Max-width centred (~1280px), white throughout. Hero is a vertically centred text stack: a
specimen icon above a large display headline, a subtitle paragraph, then two pill CTAs side by
side. Section rhythm is consistently 80px. Navigation is a floating top bar — logo left, links
centre, CTA pill right. No alternating dark/light bands.

## Icon style

Outlined, thin stroke (~1.5px), monochrome `#141414`.
