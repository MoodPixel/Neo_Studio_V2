# Krea 2 Identity Edit

## Masked-edit engine compatibility

When **Krea 2 Identity Edit** is enabled for Inpaint or Outpaint, Neo uses the Identity Edit workflow together with the **Native Inpaint/Outpaint** masked path.

While Identity Edit is active:

- **Native Inpaint / Outpaint** — available
- **Krea 2 AnyPaint** — unavailable
- **LanPaint** — unavailable

AnyPaint and LanPaint own separate masked-edit workflow architectures, so Neo does not stack them on top of Krea 2 Identity Edit. The UI disables those choices and the backend compile router also fails closed if a manually crafted request tries to combine them.

## Identity Edit controls

Identity Edit controls use a compact responsive grid.

Desktop layout:

- Row 1: **Identity Edit LoRA** · **Identity Edit LoRA Strength** · **Reference Fit**
- Row 2: **Identity Reference Boost** · **Grounding Resolution** · optional additional reference control when the active workflow exposes one

The optional Identity Edit system prompt remains full-width below the compact controls in Expert mode.

At narrower window sizes the grid automatically falls back to two columns and then one column so labels and model names stay readable.
