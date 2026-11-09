# Changelog

## 1.0 - 2026-11-09

First release.

- Export the livery diffuse as a PNG with alpha (same channels as Painter's With Alpha preset), resolution, dilation + transparent padding
- DDS conversion (NVIDIA Texture Tools, texconv, nvcompress or a custom command)
- Livery YFT build with Blender + Sollumz from the vehicle's template .yft
- File name, folder, template and texture to replace remembered per texture set; the panel follows the active texture set
- `{texture_set}` / `{vehicle}` file name patterns
- Export all: every texture set with its own settings, all YFTs built in one Blender run
