# Substance Livery Tools

A Substance 3D Painter plugin that exports vehicle liveries for GTA V straight from Painter. No more exporting the diffuse by hand, converting it to DDS and rebuilding the livery `.yft` in Blender one vehicle at a time.

For every texture set it writes:

| File | What it is |
|---|---|
| `<name>.png` | **Livery diffuse**: base color, with the layer stack's transparency in alpha |
| `<name>.dds` | **Livery texture**: the PNG compressed to DXT5 (BC3) with mipmaps |
| `<name>.yft` | **Livery model**: the vehicle's template `.yft` with the livery texture swapped in and embedded |

The PNG works on its own. The DDS and YFT need the NVIDIA Texture Tools Exporter (or another converter) and Blender with Sollumz.

---

## Features

- **One click per livery.** Export goes from the layer stack to a game-ready livery `.yft`.
- **Export all.** Export every texture set in the project in one go, each with its own settings.
- **One Blender run.** Export all builds every `.yft` in a single background Blender session, without starting Blender again for each livery.
- **Same alpha as Painter.** The PNG uses the channel setup of Painter's own *Document channels + Normal + AO (With Alpha)* preset, with dilation + transparent padding.
- **Follows your work.** The panel switches to the texture set you're working on.
- **Remembers everything.** File name, folder, template `.yft` and texture to replace are saved per texture set inside the `.spp`.
- **Name patterns.** `{texture_set}` and `{vehicle}` in the file name are filled in for each texture set.
- **Clean output.** The PNG and DDS are only kept if you tick them; otherwise they're made in a temp folder and deleted after the build.

---

## How a livery is built

| Step | What happens |
|---|---|
| **1. PNG** | Base Color + alpha of the texture set is exported at the chosen resolution and dilation |
| **2. DDS** | The PNG is converted with your DDS converter (BC3 / DXT5, mipmaps) |
| **3. Template** | Blender starts in the background and imports the vehicle's template `.yft` with Sollumz |
| **4. Swap** | The livery texture in the template is replaced by the DDS and embedded |
| **5. Export** | Sollumz exports the livery `.yft` (native or CodeWalker XML, Gen8 and/or Gen9) |

Which texture gets replaced:

| Setting | Effect |
|---|---|
| Replace texture empty | Picked automatically: diffuse textures first, preferring names with `sign` or `livery` |
| Replace texture set | Only the template texture with exactly that name is replaced |
| Texture name | What the embedded texture is called: same as the `.yft`, the template's own name, or the DDS file name |

If the texture isn't found, the log lists every texture in the template so you can pick the right one.

---

## Requirements

- **Adobe Substance 3D Painter** with Python plugin support (PySide6).
- For DDS and YFT output only:
  - **NVIDIA Texture Tools Exporter (standalone)**, or texconv / nvcompress / any converter with a command line.
    Download: <https://developer.nvidia.com/texture-tools-exporter>
    Default path: `C:\Program Files\NVIDIA Corporation\NVIDIA Texture Tools\nvtt_export.exe` (can be changed in the panel)
  - **Blender** with **[Sollumz](https://github.com/Sollumz/Sollumz)** installed and enabled.
    Default path: `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe` (can be changed in the panel)

---

## Installation

1. In Painter, open **Python → Plugins Folder**. An Explorer window opens.
2. Copy `substance_livery_tools.py` (in the root of this repo) into that folder.
3. In Painter, choose **Python → Reload Plugins Folder**.
4. Tick **substance_livery_tools** in the Python menu.

The **Substance Livery Tools** panel opens as a dock. If it's hidden, find it under **Window → Views**.

To update, replace the file, then restart Painter. Reload Plugins Folder doesn't reload a plugin that is already loaded.

---

## Usage

1. **Pick the texture set.** Click it in Painter; the panel follows. The **⟳** button refreshes the list.
2. **Set the export.** Choose a **Resolution**, a **File name**, a **Folder** and the **Dilation** (10 px by default).
3. **Set up the YFT (optional).** Tick **Build livery YFT (Sollumz)** and set:
   - the **DDS converter** and **Blender** paths (saved for every project),
   - the vehicle's **Template** `.yft` and, if needed, the **Replace texture** name (saved per texture set),
   - the **Format**, **Gen8** / **Gen9** and **Texture name**,
   - **Keep PNG** / **Keep DDS** if you want those files too.
4. Click **Export**. The log box shows the DDS conversion and Blender's output.

### Exporting several liveries

1. Set up each texture set once: pick it and fill in its file name, folder and template.
2. Click **Export all…**. Every texture set is listed with its file name and folder; the ones that are fully set up are ticked.
3. Click **Export**. Painter exports each PNG and DDS, then one Blender run builds all the `.yft` files. **Stop** cancels the rest.
4. A summary lists what was exported and what failed. One failure doesn't stop the others.

### File name patterns

| Pattern | Texture set `car_0075` gives |
|---|---|
| `{texture_set}_livery` | `car_0075_livery` |
| `{vehicle}_livery` | `car_livery` |
| `{vehicle}_liv_1` | `car_liv_1` |

`{vehicle}` is the texture set name without a trailing number. A new texture set starts with the last file name and folder you used, so a pattern only has to be typed once.

---

## Tips & troubleshooting

- **PNG has no transparency?** The texture set needs an opacity channel; the log says when no alpha could be set up.
- **"DDS converter not found"?** Point the converter field at your install, or untick **Build livery YFT** to export the PNG only.
- **"Sollumz isn't enabled in this Blender"?** Enable Sollumz in the Blender you pointed the panel at.
- **"Texture … not found in the template"?** Read the list of template textures in the log and type the right one in **Replace texture**.
- **Blender prints errors from other add-ons?** Background Blender loads your enabled add-ons too. Their errors are usually harmless; disable add-ons that don't support your Blender version.
- **Texture set not ticked in Export all?** It has no existing folder yet, or no template `.yft` while **Build livery YFT** is on. Pick it in the panel and set them.
- **Panel missing after restarting Painter?** It shows itself a moment after start-up; otherwise use **Window → Views** or **Window → Reset UI**.
- Check new liveries in CodeWalker or OpenIV before shipping them.

Global settings (converter, Blender, format, keep options) are stored on your PC. Per texture set settings are stored inside the `.spp`, so they travel with the project.

---

## Credits

- **Tidemo**: idea, design, testing and GTA V workflow
- **Claude** (Anthropic): code and documentation

Built with:

- [Adobe Substance 3D Painter](https://www.adobe.com/products/substance3d/apps/painter.html) and its Python API
- [Blender](https://www.blender.org/) and [Sollumz](https://github.com/Sollumz/Sollumz) for building the livery `.yft`
- [NVIDIA Texture Tools Exporter](https://developer.nvidia.com/texture-tools-exporter) for DDS compression
- [Qt for Python (PySide)](https://doc.qt.io/qtforpython-6/) for the interface

GTA V and Grand Theft Auto are trademarks of Rockstar Games / Take-Two Interactive. This is an unofficial fan tool and is not affiliated with or endorsed by Rockstar Games.
