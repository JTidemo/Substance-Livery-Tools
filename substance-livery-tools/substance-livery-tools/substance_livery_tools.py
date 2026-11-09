"""
Substance Livery Tools - Substance 3D Painter plugin.
By Tidemo and Claude.

Exports vehicle liveries straight from Painter:

1. Export
   Base Color + alpha of a texture set as a PNG (same result as Painter's
   "Document channels + Normal + AO (With Alpha)" preset), with resolution,
   file name, folder and dilation + transparent padding.

2. Build livery YFT (optional)
   Converts the PNG to DDS (NVIDIA Texture Tools, texconv, nvcompress or your own
   command), then runs Blender in the background with Sollumz: imports the vehicle's
   template .yft, swaps the livery texture for the DDS (embedded) and exports the
   livery .yft. PNG / DDS are deleted afterwards unless you keep them.

3. Per texture set
   File name, folder, template .yft and texture to replace are remembered for every
   texture set in the project, and the panel follows the texture set you're on.

4. Export all
   Exports every ticked texture set with its own settings; all YFTs are built in a
   single Blender run.

File names can use {texture_set} (e.g. car_0075) and {vehicle} (the same without a
trailing number, e.g. car).

Install: copy this file to
  Documents/Adobe/Adobe Substance 3D Painter/python/plugins/
then enable it in Painter's "Python" menu.
"""

import os
import re
import shutil
import tempfile

from PySide6 import QtCore, QtWidgets

import substance_painter.event as sp_event
import substance_painter.export as sp_export
import substance_painter.logging as sp_log
import substance_painter.project as sp_project
import substance_painter.textureset as sp_ts
import substance_painter.ui as sp_ui

PLUGIN = "LiveryTools"
TITLE = "Substance Livery Tools"
VERSION = "1.0"
RESOLUTIONS = [256, 512, 1024, 2048, 4096, 8192]
DEFAULT_NAME = "{texture_set}_livery"

_PLUGIN = None


def _log(msg):
    try:
        sp_log.info("[Livery Tools] " + msg)
    except Exception:  # noqa: BLE001
        print("[Livery Tools] " + msg)


def _ts_name(ts):
    """Texture set name: a property in current Painter (calling it logs a warning)."""
    n = ts.name
    return str(n) if isinstance(n, str) else n()


def vehicle_of(ts_name):
    """'car_0075' -> 'car' (the texture set name without a trailing number)."""
    return re.sub(r"[_\-]?\d+$", "", ts_name) or ts_name


def resolve_name(pattern, ts_name):
    name = (pattern.strip() or DEFAULT_NAME)
    name = name.replace("{texture_set}", ts_name).replace("{vehicle}", vehicle_of(ts_name))
    return re.sub(r'[<>:"/\\|?*]', "_", name)


def _flexible(label, wrap=True):
    """Info labels must never force the dock wider: wrap and let the width shrink."""
    label.setWordWrap(wrap)
    label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
    label.setMinimumWidth(0)
    return label


# =========================================================================== export

WITH_ALPHA_PRESET = "Document channels + Normal + AO (With Alpha)"


def _preset_basecolor_channels(stack, log=None):
    """
    Channel setup of the Base Color map from Painter's own
    "Document channels + Normal + AO (With Alpha)" preset, so the alpha is exactly
    what a manual export with that preset gives (the layer stack's transparency).
    """
    try:
        presets = sp_export.list_predefined_export_presets()
    except Exception as e:  # noqa: BLE001
        if log:
            log("could not list predefined presets: %s" % e)
        return None
    preset = next((p for p in presets if p.name == WITH_ALPHA_PRESET), None) or next(
        (p for p in presets if "document channels" in p.name.lower()
         and "with alpha" in p.name.lower()), None)
    if preset is None:
        if log:
            log("preset '%s' not found" % WITH_ALPHA_PRESET)
        return None
    for m in preset.list_output_maps(stack):
        chans = m.get("channels") or []
        rgb = [c for c in chans if c.get("destChannel") in ("R", "G", "B")]
        if rgb and all(str(c.get("srcMapName", "")).lower() == "basecolor" for c in rgb):
            return [dict(c) for c in chans]
    if log:
        log("no Base Color map in preset '%s'" % preset.name)
    return None


def export_diffuse(texture_set, size, file_name, folder, dilation):
    """Export Base Color + alpha of one texture set as a single PNG."""
    ts = sp_ts.TextureSet.from_name(texture_set)
    stack = ts.get_stack()
    channels = _preset_basecolor_channels(stack, _log)
    fallback = channels is None
    if fallback:
        # fallback: Base Color + Opacity channel as alpha
        channels = [{"destChannel": c, "srcChannel": c, "srcMapType": "documentMap",
                     "srcMapName": "basecolor"} for c in "RGB"]
        if stack.has_channel(sp_ts.ChannelType.Opacity):
            channels.append({"destChannel": "A", "srcChannel": "L", "srcMapType": "documentMap",
                             "srcMapName": "opacity"})
    has_alpha = any(c.get("destChannel") == "A" for c in channels)
    name = os.path.splitext(file_name.strip())[0] or "$textureSet_diffuse"
    params = {"fileFormat": "png", "bitDepth": "8", "dithering": False,
              "sizeLog2": int(size).bit_length() - 1,
              "paddingAlgorithm": "transparent", "dilationDistance": int(dilation)}
    config = {
        "exportShaderParams": False,
        "exportPath": folder,
        "defaultExportPreset": "LiveryToolsDiffuse",
        "exportPresets": [{"name": "LiveryToolsDiffuse",
                           "maps": [{"fileName": name, "channels": channels,
                                     "parameters": params}]}],
        "exportList": [{"rootPath": texture_set}],
        "exportParameters": [{"parameters": params}],
    }
    try:
        result = sp_export.export_project_textures(config)
    except ValueError:
        if not (fallback and has_alpha):
            raise
        # some versions name the grayscale source channel "A" instead of "L"
        channels[-1]["srcChannel"] = "A"
        result = sp_export.export_project_textures(config)
    files = [f for v in result.textures.values() for f in v]
    return result, files, has_alpha



# =========================================================================== livery yft

DEFAULT_BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
CONVERTERS = {
    # NVIDIA Texture Tools Exporter: BC3 (DXT5) with alpha, mipmaps are generated by default
    "NVIDIA Texture Tools": '"{exe}" "{in}" -f bc3 -o "{out}"',
    "texconv": '"{exe}" -f BC3_UNORM -m 0 -y -nologo -o "{outdir}" "{in}"',
    "nvcompress": '"{exe}" -bc3 "{in}" "{out}"',
    "Custom": '',
}
DEFAULT_EXES = {
    "NVIDIA Texture Tools": r"C:\Program Files\NVIDIA Corporation\NVIDIA Texture Tools\nvtt_export.exe",
}
# filled in by the packager with the Blender-side script (blender_livery_job.py)
BLENDER_JOB_SOURCE = '"""\nBackground Blender job: build one livery .yft from a template with Sollumz.\nBased on the "Sollumz Livery YFT Batch" add-on.\n\nRun:  blender.exe -b --python blender_livery_job.py -- <job.json>\n\njob.json keys:\n  template    path to the template .yft / .yft.xml\n  dds         path to the livery .dds\n  name        output yft name\n  output_dir  output folder\n  target      texture to replace (template texture name, \'\' = automatic)\n  diffuse_only, fmt ("NATIVE"/"CWXML"), versions (["GEN8"], ...), tex_naming ("YFT"/"TEMPLATE"/"DDS")\n\nA job file may also be {"jobs": [job, job, ...]}: every livery is built in this one\nBlender session (no restart per livery) and the result carries "results": [...].\n\nPrints one line "LIVERYTOOLS_RESULT <json>" and exits with 0 on success.\n"""\n\nimport json\nimport os\nimport re\nimport shutil\nimport sys\nimport tempfile\nimport time\nimport traceback\nfrom pathlib import Path\n\nimport bpy\n\nFRAGMENT = "sollumz_fragment"\nLOD_ATTRS = ("very_high", "high", "medium", "low", "very_low")\n\n\nclass JobFailed(Exception):\n    def __init__(self, info):\n        super().__init__(info.get("error", "failed"))\n        self.info = info\n\n\ndef fail(**kw):\n    """Stop the current livery with an error (the batch carries on with the next one)."""\n    kw["ok"] = False\n    raise JobFailed(kw)\n\n\ndef result(ok, **kw):\n    kw["ok"] = ok\n    print("LIVERYTOOLS_RESULT " + json.dumps(kw), flush=True)\n    sys.stdout.flush()\n    os._exit(0 if ok else 1)\n\n\ndef log(msg):\n    print("[livery] " + msg, flush=True)\n\n\n# ------------------------------------------------------------------ helpers (from the add-on)\n\ndef stype(o):\n    try:\n        return str(o.sollum_type)\n    except Exception:\n        return ""\n\n\ndef clean_name(s):\n    s = s.strip().lower()\n    s = re.sub(r"[.\\s]+", "_", s)\n    s = re.sub(r"[^a-z0-9_\\-]", "", s)\n    return s or "livery"\n\n\ndef frag_meshes(frag):\n    meshes = []\n    for o in frag.children_recursive:\n        if o.type != "MESH":\n            continue\n        if o.data and o.data not in meshes:\n            meshes.append(o.data)\n        lods = getattr(o, "sz_lods", None)\n        if lods is None:\n            continue\n        for a in LOD_ATTRS:\n            lod = getattr(lods, a, None)\n            me = getattr(lod, "mesh", None) if lod is not None else None\n            if me is not None and me not in meshes:\n                meshes.append(me)\n    return meshes\n\n\ndef image_nodes(frag):\n    out = []\n    mats = []\n    for me in frag_meshes(frag):\n        for m in me.materials:\n            if m and m.use_nodes and m not in mats:\n                mats.append(m)\n    for m in mats:\n        for n in m.node_tree.nodes:\n            if n.bl_idname == "ShaderNodeTexImage":\n                out.append((m, n))\n    return out\n\n\ndef tex_name(img):\n    if not img:\n        return ""\n    return os.path.splitext(bpy.path.basename(img.filepath))[0].lower() or img.name.lower()\n\n\ndef pick_target(frag, wanted):\n    """Image to replace: the one named `wanted`, else the best diffuse livery/sign texture."""\n    seen = {}\n    for m, n in image_nodes(frag):\n        if not n.image:\n            continue\n        e = seen.setdefault(n.image.name, {"img": n.image, "diffuse": False})\n        if "diffuse" in n.name.lower():\n            e["diffuse"] = True\n    if not seen:\n        return None, []\n    names = sorted(tex_name(e["img"]) for e in seen.values())\n    if wanted:\n        w = wanted.strip().lower()\n        for e in seen.values():\n            if tex_name(e["img"]) == w or e["img"].name.lower() == w:\n                return e["img"], names\n        return None, names\n    ranked = sorted(seen.values(),\n                    key=lambda e: (not e["diffuse"],\n                                   not any(k in tex_name(e["img"]) for k in ("sign", "livery")),\n                                   tex_name(e["img"])))\n    return ranked[0]["img"], names\n\n\ndef export_kwargs(job):\n    wanted = {\n        "directory": job["output_dir"],\n        "direct_export": True,\n        "use_custom_settings": True,\n        "target_formats": {job.get("fmt", "NATIVE")},\n        "target_versions": set(job.get("versions") or ["GEN8"]),\n        "limit_to_selected": True,\n        "apply_transforms": False,\n        "exclude_skeleton": False,\n        "export_ytyps": False,\n        "export_ymaps": False,\n        "export_ytds": False,\n    }\n    props = bpy.ops.sollumz.export_assets.get_rna_type().properties.keys()\n    return {k: v for k, v in wanted.items() if k in props}\n\n\ndef expected_outputs(job, name):\n    ext = ".yft" if job.get("fmt", "NATIVE") == "NATIVE" else ".yft.xml"\n    vers = sorted(job.get("versions") or ["GEN8"])\n    if len(vers) > 1:\n        return [Path(job["output_dir"]) / v.lower() / f"{name}{ext}" for v in vers]\n    return [Path(job["output_dir"]) / f"{name}{ext}"]\n\n\ndef ensure_sollumz():\n    if hasattr(bpy.ops, "sollumz") and hasattr(bpy.ops.sollumz, "export_assets"):\n        return True\n    try:\n        import addon_utils\n        for mod in addon_utils.modules():\n            if "sollumz" in mod.__name__.lower():\n                addon_utils.enable(mod.__name__, default_set=False, persistent=False)\n                log("enabled add-on " + mod.__name__)\n                break\n    except Exception:\n        traceback.print_exc()\n    return hasattr(bpy.ops, "sollumz") and hasattr(bpy.ops.sollumz, "export_assets")\n\n\n# ------------------------------------------------------------------ main\n\ndef main():\n    job_path = sys.argv[sys.argv.index("--") + 1]\n    with open(job_path, encoding="utf-8") as f:\n        data = json.load(f)\n    if not ensure_sollumz():\n        result(False, error="Sollumz isn\'t enabled in this Blender")\n    jobs = data.get("jobs") if isinstance(data, dict) and "jobs" in data else None\n    if jobs is None:  # a single livery\n        try:\n            res = build_one(data)\n        except JobFailed as e:\n            result(False, **{k: v for k, v in e.info.items() if k != "ok"})\n        result(True, **{k: v for k, v in res.items() if k != "ok"})\n    results = []\n    for i, job in enumerate(jobs):\n        log("--- livery %d of %d: %s" % (i + 1, len(jobs), job.get("name") or job.get("dds")))\n        try:\n            results.append(build_one(job))\n        except JobFailed as e:\n            results.append(e.info)\n        except Exception as e:  # noqa: BLE001\n            traceback.print_exc()\n            results.append({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})\n        results[-1].setdefault("name", job.get("name"))\n    result(all(r.get("ok") for r in results), results=results)\n\n\ndef build_one(job):\n    for k in ("template", "dds"):\n        if not os.path.isfile(job.get(k, "")):\n            fail(error="%s not found: %s" % (k, job.get(k)))\n    os.makedirs(job["output_dir"], exist_ok=True)\n    name = clean_name(job.get("name") or Path(job["dds"]).stem)\n\n    if job.get("fmt", "NATIVE") == "NATIVE":\n        try:\n            from szio.gta5 import AssetFormat, is_provider_available\n            native_ok = is_provider_available(AssetFormat.NATIVE)\n        except Exception:\n            native_ok = True  # older Sollumz: no check available\n        if not native_ok:\n            fail(error="Sollumz native export isn\'t available - choose CW XML")\n\n    # fresh scene, then import the template\n    bpy.ops.wm.read_homefile(use_empty=True)\n    if not ensure_sollumz():\n        fail(error="Sollumz isn\'t enabled in this Blender")\n    tpl = Path(job["template"])\n    before = set(bpy.data.objects)\n    log("importing template " + str(tpl))\n    bpy.ops.sollumz.import_assets(directory=str(tpl.parent), files=[{"name": tpl.name}])\n    frags = [o for o in bpy.data.objects if o not in before and stype(o) == FRAGMENT]\n    if not frags:\n        fail(error="No fragment was imported from the template")\n    frag = frags[0]\n\n    img_old, available = pick_target(frag, job.get("target", ""))\n    if img_old is None:\n        fail(error="Texture \'%s\' not found in the template" % job.get("target"),\n               textures=available)\n    diffuse_only = job.get("diffuse_only", True)\n    nodes = [n for m, n in image_nodes(frag)\n             if n.image == img_old and (not diffuse_only or "diffuse" in n.name.lower())]\n    if not nodes:\n        fail(error="No matching texture slots for \'%s\'" % tex_name(img_old),\n               textures=available)\n    log("replacing \'%s\' in %d slot(s)" % (tex_name(img_old), len(nodes)))\n\n    # Sollumz names an embedded texture after its file: stage a renamed copy if needed\n    want = {"YFT": name, "TEMPLATE": clean_name(tex_name(img_old)), "DDS": None}[\n        job.get("tex_naming", "YFT")]\n    path = job["dds"]\n    stage = tempfile.mkdtemp(prefix="liverytools_")\n    try:\n        if want and want != Path(path).stem.lower():\n            staged = os.path.join(stage, want + ".dds")\n            shutil.copyfile(path, staged)\n            path = staged\n        img = bpy.data.images.load(path, check_existing=False)\n        for n in nodes:\n            n.image = img\n            n.texture_properties.embedded = True\n\n        frag.name = name\n        for o in bpy.context.view_layer.objects:\n            o.select_set(False)\n        frag.hide_set(False)\n        frag.hide_viewport = False\n        frag.select_set(True)\n        bpy.context.view_layer.objects.active = frag\n\n        outs = expected_outputs(job, name)\n        t0 = time.time() - 1\n        log("exporting " + ", ".join(map(str, outs)))\n        bpy.ops.sollumz.export_assets(**export_kwargs(job))\n        good = [str(p) for p in outs if p.exists() and p.stat().st_mtime >= t0]\n    finally:\n        shutil.rmtree(stage, ignore_errors=True)\n    if len(good) != len(outs):\n        fail(error="Sollumz did not write " + ", ".join(map(str, outs)),\n               textures=available)\n    return {"ok": True, "files": good, "replaced": tex_name(img_old)}\n\n\ntry:\n    main()\nexcept SystemExit:\n    raise\nexcept Exception as e:  # noqa: BLE001\n    traceback.print_exc()\n    result(False, error="%s: %s" % (type(e).__name__, e))\n'


def _blender_job_source():
    if BLENDER_JOB_SOURCE:
        return BLENDER_JOB_SOURCE
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_livery_job.py")
    with open(here, encoding="utf-8") as f:
        return f.read()


def convert_to_dds(png, preset, exe, custom_cmd):
    """Run the DDS converter on `png`; returns the .dds path."""
    import subprocess
    out = os.path.splitext(png)[0] + ".dds"
    cmd = custom_cmd if preset == "Custom" else CONVERTERS[preset]
    if not cmd.strip():
        raise RuntimeError("Enter a converter command line")
    if "{exe}" in cmd and not os.path.isfile(exe):
        raise RuntimeError("DDS converter not found: %s" % exe)
    line = (cmd.replace("{exe}", exe).replace("{in}", png).replace("{out}", out)
            .replace("{outdir}", os.path.dirname(png))
            .replace("{name}", os.path.splitext(os.path.basename(png))[0]))
    t0 = os.path.getmtime(out) if os.path.exists(out) else None
    flags = 0x08000000 if os.name == "nt" else 0  # no console window
    r = subprocess.run(line, shell=True, capture_output=True, text=True, creationflags=flags)
    fresh = os.path.exists(out) and (t0 is None or os.path.getmtime(out) > t0)
    if r.returncode != 0 or not fresh:
        raise RuntimeError("DDS conversion failed (exit %s):\n%s\n%s"
                           % (r.returncode, line, (r.stderr or r.stdout).strip()[-1500:]))
    return out


class YftBuilder(QtCore.QObject):
    """Runs the Sollumz livery job in a background Blender process."""
    finished = QtCore.Signal(dict)
    output = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self.proc = None
        self._buf = ""
        self._result = None

    def running(self):
        return self.proc is not None and self.proc.state() != QtCore.QProcess.NotRunning

    def start(self, blender, job):
        import json, tempfile
        if not os.path.isfile(blender):
            raise RuntimeError("Blender not found: %s" % blender)
        work = tempfile.mkdtemp(prefix="liverytools_")
        self._work = work
        script = os.path.join(work, "livery_job.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(_blender_job_source())
        job_path = os.path.join(work, "job.json")
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(job, f)
        self._buf, self._result = "", None
        self.proc = QtCore.QProcess(self)
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._done)
        self.proc.errorOccurred.connect(self._error)
        self.proc.start(blender, ["-b", "--python", script, "--", job_path])

    def _read(self):
        import json
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip("\r")
            if line.startswith("LIVERYTOOLS_RESULT "):
                try:
                    self._result = json.loads(line[len("LIVERYTOOLS_RESULT "):])
                except ValueError:
                    pass
            elif line.strip():
                self.output.emit(line)

    def _clean_work(self):
        work, self._work = getattr(self, "_work", None), None
        if work:
            shutil.rmtree(work, ignore_errors=True)

    def _done(self, *_):
        self._read()
        self._clean_work()
        res = self._result or {"ok": False,
                               "error": "Blender exited without a result (see the log above)"}
        self.proc = None
        self.finished.emit(res)

    def _error(self, err):
        if self.proc is not None and self.proc.state() == QtCore.QProcess.NotRunning:
            self.proc = None
            self._clean_work()
            self.finished.emit({"ok": False, "error": "Could not start Blender (%s)" % err})



# =========================================================================== panel

class LiveryPanel(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QtCore.QSettings("LiveryTools", "Painter")
        self._batch = None
        self._job_files = None
        self._yft_cb = None
        self._loading = False
        self._active = None

        # --- export
        export_box = QtWidgets.QGroupBox("Export")
        self.ts_combo = QtWidgets.QComboBox()
        self.ts_combo.setToolTip("Follows the texture set you're working on")
        refresh = QtWidgets.QToolButton(); refresh.setText("⟳")
        refresh.setToolTip("Refresh the texture set list")
        refresh.clicked.connect(self.refresh_texture_sets)
        self.res_combo = QtWidgets.QComboBox()
        self.res_combo.addItems([str(r) for r in RESOLUTIONS])
        self.res_combo.setCurrentText(str(self.settings.value("res", "2048")))
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText(DEFAULT_NAME)
        self.name_edit.setToolTip("File name without extension.\n"
                                  "{texture_set} = the texture set's name (car_0075)\n"
                                  "{vehicle} = the same without a trailing number (car)")
        self.name_preview = _flexible(QtWidgets.QLabel())
        self.folder_edit = QtWidgets.QLineEdit()
        self.folder_edit.setPlaceholderText("choose a folder")
        browse = QtWidgets.QToolButton(); browse.setText("...")
        browse.clicked.connect(lambda: self._pick_dir(self.folder_edit))
        self.dilation = QtWidgets.QSpinBox(); self.dilation.setRange(0, 256)
        self.dilation.setValue(int(self.settings.value("dilation", 10)))
        self.dilation.setSuffix(" px")
        export_btn = QtWidgets.QPushButton("Export")
        export_btn.clicked.connect(self._export)
        all_btn = QtWidgets.QPushButton("Export all...")
        all_btn.setToolTip("Export several texture sets in one go, each with its own name, "
                           "folder and template; YFTs are built in one Blender run")
        all_btn.clicked.connect(self._export_all)
        ef = QtWidgets.QFormLayout(export_box)
        tr = QtWidgets.QHBoxLayout(); tr.addWidget(self.ts_combo, 1); tr.addWidget(refresh)
        ef.addRow("Texture set", tr)
        ef.addRow("Resolution", self.res_combo)
        ef.addRow("File name", self.name_edit)
        ef.addRow("", self.name_preview)
        fr = QtWidgets.QHBoxLayout(); fr.addWidget(self.folder_edit, 1); fr.addWidget(browse)
        ef.addRow("Folder", fr)
        ef.addRow("Dilation", self.dilation)
        br = QtWidgets.QHBoxLayout(); br.addWidget(export_btn, 1); br.addWidget(all_btn, 1)
        ef.addRow(br)

        # --- livery yft
        self.builder = YftBuilder()
        self.builder.output.connect(self._yft_log)
        self.builder.finished.connect(self._yft_done)
        self.yft_box = QtWidgets.QGroupBox("Build livery YFT (Sollumz)")
        self.yft_box.setCheckable(True)
        self.yft_box.setChecked(self.settings.value("yft_on", "false") == "true")
        self.yft_box.setToolTip("After exporting the PNG: convert it to DDS and build the "
                                "livery yft from the vehicle's template in background Blender")
        self.conv_combo = QtWidgets.QComboBox(); self.conv_combo.addItems(list(CONVERTERS))
        self.conv_combo.setCurrentText(self.settings.value("conv", "NVIDIA Texture Tools"))
        self.conv_exe = self._file_row(
            self.settings.value("conv_exe", "") or DEFAULT_EXES.get(self.conv_combo.currentText(), ""),
            "DDS converter", "Programs (*.exe);;All files (*)")
        self.conv_cmd = QtWidgets.QLineEdit(self.settings.value("conv_cmd", ""))
        self.conv_cmd.setPlaceholderText('"{exe}" ... "{in}" ... "{out}"   ({outdir} {name} also work)')
        self.blender = self._file_row(self.settings.value("blender", DEFAULT_BLENDER),
                                      "Blender", "Blender (blender.exe);;All files (*)")
        self.template = self._file_row("", "Template YFT", "Template (*.yft *.yft.xml)")
        self.template[0].setToolTip("The vehicle's livery template - remembered per texture set")
        self.target_edit = QtWidgets.QLineEdit()
        self.target_edit.setPlaceholderText("automatic (diffuse livery/sign texture)")
        self.fmt_combo = QtWidgets.QComboBox()
        self.fmt_combo.addItem("Native .yft", "NATIVE"); self.fmt_combo.addItem("CW XML", "CWXML")
        self.fmt_combo.setCurrentIndex(int(self.settings.value("fmt", 0)))
        self.gen8 = QtWidgets.QCheckBox("Gen8")
        self.gen8.setToolTip("GTA V Legacy / FiveM")
        self.gen8.setChecked(self.settings.value("gen8", "true") == "true")
        self.gen9 = QtWidgets.QCheckBox("Gen9")
        self.gen9.setToolTip("GTA V Enhanced")
        self.gen9.setChecked(self.settings.value("gen9", "false") == "true")
        self.texname_combo = QtWidgets.QComboBox()
        self.texname_combo.addItem("Same as YFT", "YFT")
        self.texname_combo.addItem("Keep template's", "TEMPLATE")
        self.texname_combo.addItem("DDS file name", "DDS")
        self.texname_combo.setCurrentIndex(int(self.settings.value("texname", 0)))
        self.keep_png = QtWidgets.QCheckBox("Keep PNG")
        self.keep_png.setChecked(self.settings.value("keep_png", "false") == "true")
        self.keep_dds = QtWidgets.QCheckBox("Keep DDS")
        self.keep_dds.setChecked(self.settings.value("keep_dds", "false") == "true")
        for cb in (self.keep_png, self.keep_dds):
            cb.setToolTip("Unticked: the file is only made for the YFT build and deleted afterwards")
        self.yft_log_box = QtWidgets.QPlainTextEdit(); self.yft_log_box.setReadOnly(True)
        self.yft_log_box.setMaximumBlockCount(400); self.yft_log_box.setFixedHeight(110)
        self.conv_combo.currentTextChanged.connect(self._conv_changed)
        self._conv_changed(self.conv_combo.currentText())
        yf = QtWidgets.QFormLayout(self.yft_box)
        cr = QtWidgets.QHBoxLayout(); cr.addWidget(self.conv_combo); cr.addWidget(self.conv_exe[1], 1)
        yf.addRow("DDS converter", cr)
        yf.addRow("Command", self.conv_cmd)
        yf.addRow("Blender", self.blender[1])
        yf.addRow("Template", self.template[1])
        yf.addRow("Replace texture", self.target_edit)
        fr2 = QtWidgets.QHBoxLayout(); fr2.addWidget(self.fmt_combo); fr2.addWidget(self.gen8)
        fr2.addWidget(self.gen9); fr2.addStretch()
        yf.addRow("Format", fr2)
        yf.addRow("Texture name", self.texname_combo)
        kr = QtWidgets.QHBoxLayout(); kr.addWidget(self.keep_png); kr.addWidget(self.keep_dds)
        kr.addStretch()
        yf.addRow("Also keep", kr)
        yf.addRow(self.yft_log_box)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(export_box)
        lay.addWidget(self.yft_box)
        lay.addStretch()

        # per texture set fields are saved as you edit them
        for w in (self.name_edit, self.folder_edit, self.template[0], self.target_edit):
            w.textChanged.connect(self._store_current)
        self.name_edit.textChanged.connect(lambda *_: self._update_preview())
        self.ts_combo.currentTextChanged.connect(self._ts_picked)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._watch_active)
        self._update_preview()

    # ---------------------------------------------------------------- helpers
    def _message(self, text, error=False):
        _log(text)
        box = QtWidgets.QMessageBox.warning if error else QtWidgets.QMessageBox.information
        box(self, TITLE, text)

    def _file_row(self, value, title, filt):
        edit = QtWidgets.QLineEdit(value)
        btn = QtWidgets.QToolButton(); btn.setText("...")

        def browse():
            p, _ = QtWidgets.QFileDialog.getOpenFileName(self, title, edit.text(), filt)
            if p:
                edit.setText(os.path.normpath(p))
        btn.clicked.connect(browse)
        w = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1); h.addWidget(btn)
        return edit, w

    def _pick_dir(self, edit):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder", edit.text())
        if d:
            edit.setText(os.path.normpath(d))

    def _conv_changed(self, preset):
        custom = preset == "Custom"
        self.conv_cmd.setEnabled(custom)
        if not custom:
            self.conv_cmd.setText(CONVERTERS[preset])
        current = self.conv_exe[0].text().strip()
        if preset in DEFAULT_EXES and (not current or current in DEFAULT_EXES.values()
                                       or not os.path.isfile(current)):
            self.conv_exe[0].setText(DEFAULT_EXES[preset])

    def _yft_log(self, line):
        self.yft_log_box.appendPlainText(line)

    def _save_global(self):
        s = self.settings
        s.setValue("res", self.res_combo.currentText())
        s.setValue("dilation", self.dilation.value())
        s.setValue("yft_on", "true" if self.yft_box.isChecked() else "false")
        s.setValue("conv", self.conv_combo.currentText())
        s.setValue("conv_exe", self.conv_exe[0].text())
        if self.conv_combo.currentText() == "Custom":
            s.setValue("conv_cmd", self.conv_cmd.text())
        s.setValue("blender", self.blender[0].text())
        s.setValue("fmt", self.fmt_combo.currentIndex())
        s.setValue("gen8", "true" if self.gen8.isChecked() else "false")
        s.setValue("gen9", "true" if self.gen9.isChecked() else "false")
        s.setValue("texname", self.texname_combo.currentIndex())
        s.setValue("keep_png", "true" if self.keep_png.isChecked() else "false")
        s.setValue("keep_dds", "true" if self.keep_dds.isChecked() else "false")

    # ---------------------------------------------------------------- per texture set
    def _all_settings(self):
        try:
            return dict(sp_project.Metadata(PLUGIN).get("texture_sets") or {})
        except Exception:  # noqa: BLE001
            return {}

    def _settings_for(self, ts_name):
        """Saved settings of a texture set; a new one starts from the last one used."""
        d = self._all_settings().get(ts_name)
        if d:
            return dict(d)
        last = dict(self.settings.value("last_ts_settings", {}) or {})
        return {"name": last.get("name", ""), "folder": last.get("folder", ""),
                "template": "", "target": last.get("target", "")}

    def _store_current(self, *_):
        if self._loading or not sp_project.is_open():
            return
        ts = self.ts_combo.currentText()
        if not ts:
            return
        d = {"name": self.name_edit.text(), "folder": self.folder_edit.text(),
             "template": self.template[0].text(), "target": self.target_edit.text()}
        try:
            allv = self._all_settings()
            allv[ts] = d
            sp_project.Metadata(PLUGIN).set("texture_sets", allv)
        except Exception:  # noqa: BLE001
            pass
        self.settings.setValue("last_ts_settings", d)

    def _ts_picked(self, name):
        if not name:
            return
        d = self._settings_for(name)
        self._loading = True
        try:
            self.name_edit.setText(d.get("name", ""))
            self.folder_edit.setText(d.get("folder", ""))
            self.template[0].setText(d.get("template", ""))
            self.target_edit.setText(d.get("target", ""))
        finally:
            self._loading = False
        self._update_preview()

    def _update_preview(self):
        ts = self.ts_combo.currentText()
        if ts:
            self.name_preview.setText("→ %s.png" % resolve_name(self.name_edit.text(), ts))
        else:
            self.name_preview.setText("")

    def refresh_texture_sets(self):
        try:
            active = _ts_name(sp_ts.get_active_stack().material())
        except Exception:  # noqa: BLE001
            active = ""
        current = active or self.ts_combo.currentText()
        try:
            names = [_ts_name(ts) for ts in sp_ts.all_texture_sets()]
        except Exception:  # noqa: BLE001
            names = []
        self.ts_combo.blockSignals(True)
        self.ts_combo.clear()
        self.ts_combo.addItems(names)
        if current in names:
            self.ts_combo.setCurrentText(current)
        self.ts_combo.blockSignals(False)
        self._ts_picked(self.ts_combo.currentText())

    def _watch_active(self):
        """The panel follows the texture set you're working on."""
        if self._batch is not None:
            return
        try:
            if not sp_project.is_open() or sp_project.is_busy():
                return
            active = _ts_name(sp_ts.get_active_stack().material())
        except Exception:  # noqa: BLE001
            return
        if active == self._active:
            return
        self._active = active
        if self.ts_combo.findText(active) < 0:
            self.refresh_texture_sets()
        if self.ts_combo.findText(active) >= 0 and self.ts_combo.currentText() != active:
            self.ts_combo.setCurrentText(active)

    # ---------------------------------------------------------------- export
    def _check_yft(self, template):
        if self.builder.running():
            return "A YFT build is already running."
        if not os.path.isfile((template or "").strip()):
            return "Choose the vehicle's template .yft in 'Build livery YFT'."
        if not (self.gen8.isChecked() or self.gen9.isChecked()):
            return "Tick Gen8 and/or Gen9."
        return None

    def _export(self):
        if not sp_project.is_open():
            self._message("Open a project first.", error=True)
            return
        ts = self.ts_combo.currentText()
        if not ts:
            self.refresh_texture_sets(); ts = self.ts_combo.currentText()
        folder = self.folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            self._message("Choose an existing export folder.", error=True)
            return
        self._save_global()
        self._store_current()
        try:
            item = self._prepare(ts, self._settings_for(ts))
        except Exception as e:  # noqa: BLE001
            self._message(str(e), error=True)
            return
        if item.get("job") is None:
            self._message("Exported:\n" + "\n".join(item["files"]) + item.get("note", ""))
            return
        self.yft_log_box.clear()
        self._yft_log("Starting Blender (Sollumz)...")
        self._single = item
        try:
            self.builder.start(self.blender[0].text().strip(), item["job"])
        except Exception as e:  # noqa: BLE001
            self._cleanup(item)
            self._message(str(e), error=True)

    def _prepare(self, ts, cfg):
        """PNG (and DDS + Blender job when building YFTs) for one texture set.
        Raises with a readable message on failure."""
        folder = (cfg.get("folder") or "").strip()
        if not folder or not os.path.isdir(folder):
            raise RuntimeError("%s: no existing export folder set" % ts)
        name = resolve_name(cfg.get("name", ""), ts)
        build = self.yft_box.isChecked()
        if build:
            problem = self._check_yft(cfg.get("template"))
            if problem:
                raise RuntimeError("%s: %s" % (ts, problem))
        tmp = tempfile.mkdtemp(prefix="liverytools_") if build and not self.keep_png.isChecked() \
            else None
        try:
            result, files, has_alpha = export_diffuse(
                ts, int(self.res_combo.currentText()), name, tmp or folder, self.dilation.value())
        except Exception as e:  # noqa: BLE001
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError("%s: export failed: %s" % (ts, e))
        if result.status != sp_export.ExportStatus.Success:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError("%s: export finished with status %s: %s"
                               % (ts, result.status, result.message))
        note = "" if has_alpha else ("\n\nNo alpha could be set up for %s, so the PNG has no "
                                     "transparency (see the log)." % ts)
        item = {"ts": ts, "files": files, "note": note, "tmp": tmp, "png": None, "dds": None,
                "keep_png": self.keep_png.isChecked(), "keep_dds": self.keep_dds.isChecked(),
                "job": None, "folder": folder}
        if not build:
            return item
        pngs = [f for f in files if f.lower().endswith(".png")]
        if not pngs:
            self._cleanup(item)
            raise RuntimeError("%s: the export wrote no PNG" % ts)
        item["png"] = pngs[0]
        self._yft_log("%s: converting to DDS..." % ts)
        QtWidgets.QApplication.processEvents()
        try:
            dds = convert_to_dds(pngs[0], self.conv_combo.currentText(),
                                 self.conv_exe[0].text().strip(), self.conv_cmd.text())
        except Exception as e:  # noqa: BLE001
            self._cleanup(item)
            raise RuntimeError("%s: %s" % (ts, e))
        if self.keep_dds.isChecked() and os.path.dirname(dds) != folder:
            shutil.copy2(dds, os.path.join(folder, os.path.basename(dds)))
        else:
            item["dds"] = dds
        versions = [v for v, cb in (("GEN8", self.gen8), ("GEN9", self.gen9)) if cb.isChecked()]
        item["job"] = {
            "template": cfg.get("template", "").strip(), "dds": dds, "name": name,
            "output_dir": folder, "target": (cfg.get("target") or "").strip(),
            "diffuse_only": True, "fmt": self.fmt_combo.currentData(), "versions": versions,
            "tex_naming": self.texname_combo.currentData(), "label": ts,
        }
        return item

    def _cleanup(self, item):
        """Delete the intermediate files that weren't ticked to keep."""
        for path, keep in ((item.get("png"), item["keep_png"]), (item.get("dds"), item["keep_dds"])):
            if path and not keep and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError as e:
                    _log("could not delete %s: %s" % (path, e))
        if item.get("tmp"):
            shutil.rmtree(item["tmp"], ignore_errors=True)

    def _yft_done(self, res):
        if self._batch is not None:
            self._batch_yft_done(res)
            return
        item, self._single = getattr(self, "_single", None), None
        if item:
            self._cleanup(item)
        if res.get("ok"):
            self._yft_log("Done.")
            self._message("Livery YFT built (replaced '%s'):\n%s"
                          % (res.get("replaced", "?"), "\n".join(res.get("files", []))))
        else:
            msg = res.get("error", "unknown error")
            if res.get("textures"):
                msg += "\n\nTextures in the template:\n" + ", ".join(res["textures"])
            self._yft_log("FAILED: " + msg)
            self._message("YFT build failed:\n" + msg, error=True)

    # ---------------------------------------------------------------- export all
    def _export_all(self):
        if not sp_project.is_open():
            self._message("Open a project first.", error=True)
            return
        if self.builder.running():
            self._message("A YFT build is already running.", error=True)
            return
        self._save_global()
        self._store_current()
        try:
            names = [_ts_name(ts) for ts in sp_ts.all_texture_sets()]
        except Exception as e:  # noqa: BLE001
            self._message("Could not list the texture sets: %s" % e, error=True)
            return
        saved = self._all_settings()
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Export all")
        outer = QtWidgets.QVBoxLayout(dlg)
        outer.addWidget(QtWidgets.QLabel(
            "Each texture set is exported with its own name, folder%s.\n"
            "Set those by picking the texture set in the panel."
            % (" and template" if self.yft_box.isChecked() else "")))
        grid_w = QtWidgets.QWidget(); grid = QtWidgets.QGridLayout(grid_w)
        for col, h in enumerate(("Texture set", "File", "Folder")):
            grid.addWidget(QtWidgets.QLabel("<b>%s</b>" % h), 0, col)
        rows = []
        for r, ts in enumerate(names, 1):
            cfg = saved.get(ts)
            cb = QtWidgets.QCheckBox(ts)
            ok = bool(cfg and cfg.get("folder") and os.path.isdir(cfg["folder"]))
            if ok and self.yft_box.isChecked():
                ok = os.path.isfile((cfg.get("template") or "").strip())
            cb.setChecked(ok)
            file_lbl = QtWidgets.QLabel(resolve_name((cfg or {}).get("name", ""), ts)
                                        if cfg else "(not set up)")
            folder_lbl = QtWidgets.QLabel((cfg or {}).get("folder", "") or "-")
            if not ok:
                cb.setToolTip("Pick this texture set in the panel and set its folder%s first"
                              % (" and template" if self.yft_box.isChecked() else ""))
            grid.addWidget(cb, r, 0); grid.addWidget(file_lbl, r, 1); grid.addWidget(folder_lbl, r, 2)
            rows.append((ts, cb))
        scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(grid_w)
        scroll.setMinimumHeight(min(60 + 28 * len(rows), 500))
        outer.addWidget(scroll, 1)
        sb = getattr(QtWidgets.QDialogButtonBox, "StandardButton", QtWidgets.QDialogButtonBox)
        buttons = QtWidgets.QDialogButtonBox(sb.Ok | sb.Cancel)
        buttons.button(sb.Ok).setText("Export")
        buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject)
        outer.addWidget(buttons)
        dlg.resize(700, dlg.sizeHint().height())
        accepted = getattr(QtWidgets.QDialog, "DialogCode", QtWidgets.QDialog).Accepted
        if dlg.exec() != accepted:
            return
        queue = [ts for ts, cb in rows if cb.isChecked()]
        if not queue:
            return
        progress = QtWidgets.QProgressDialog("Exporting...", "Stop", 0, len(queue), self)
        progress.setWindowTitle("Export all")
        progress.setMinimumDuration(0)
        self._batch = {"queue": queue, "total": len(queue), "done": [], "failed": [],
                       "items": [], "progress": progress}
        self.yft_log_box.clear()
        QtCore.QTimer.singleShot(0, self._batch_next)

    def _batch_next(self):
        b = self._batch
        if b["progress"].wasCanceled() and b["queue"]:
            b["failed"] += ["%s: stopped" % ts for ts in b["queue"]]
            b["queue"] = []
        if not b["queue"]:
            self._batch_finish()
            return
        ts = b["queue"].pop(0)
        b["progress"].setLabelText("%s  (%d of %d)" % (ts, b["total"] - len(b["queue"]), b["total"]))
        b["progress"].setValue(b["total"] - len(b["queue"]) - 1)
        QtWidgets.QApplication.processEvents()
        try:
            item = self._prepare(ts, self._settings_for(ts))
            b["items"].append(item)
            if item.get("job") is None:
                b["done"].append("%s → %s" % (ts, ", ".join(item["files"])))
        except Exception as e:  # noqa: BLE001
            b["failed"].append(str(e))
        QtCore.QTimer.singleShot(50, self._batch_next)

    def _batch_finish(self):
        b = self._batch
        jobs = [i for i in b["items"] if i.get("job")]
        if jobs and not b.get("yft_started"):
            b["yft_started"] = True
            b["progress"].setLabelText("Building %d livery YFT%s in one Blender run..."
                                       % (len(jobs), "" if len(jobs) == 1 else "s"))
            b["progress"].setRange(0, 0)
            self._yft_log("Starting Blender (Sollumz) for %d liveries..." % len(jobs))
            try:
                self.builder.start(self.blender[0].text().strip(),
                                   {"jobs": [i["job"] for i in jobs]})
            except Exception as e:  # noqa: BLE001
                self._batch_yft_done({"ok": False, "error": str(e)})
            return
        b["progress"].setRange(0, max(b["total"], 1))
        b["progress"].setValue(b["total"])
        b["progress"].close()
        self._batch = None
        lines = ["Exported %d of %d:" % (len(b["done"]), b["total"])] + b["done"]
        if b["failed"]:
            lines += ["", "Failed:"] + b["failed"]
        self._message("\n".join(lines), error=bool(b["failed"]))

    def _batch_yft_done(self, res):
        b = self._batch
        jobs = [i for i in b["items"] if i.get("job")]
        results = res.get("results")
        if results is None:  # Blender failed as a whole
            results = [{"ok": False, "error": res.get("error", "unknown error")}] * len(jobs)
        for item, r in zip(jobs, results):
            self._cleanup(item)
            if r.get("ok"):
                b["done"].append("%s → %s" % (item["ts"], ", ".join(r.get("files", []))))
            else:
                msg = r.get("error", "unknown error")
                if r.get("textures"):
                    msg += " (textures in the template: %s)" % ", ".join(r["textures"])
                b["failed"].append("%s: YFT build failed - %s" % (item["ts"], msg))
        for item in jobs:
            item["job"] = None
        self._yft_log("Done.")
        self._batch_finish()

    # ---------------------------------------------------------------- project events
    def on_project_opened(self, *_):
        self._active = None
        self.refresh_texture_sets()
        self._timer.start()

    def on_project_closed(self, *_):
        self._timer.stop()
        self._loading = True
        try:
            self.ts_combo.clear()
            for w in (self.name_edit, self.folder_edit, self.template[0], self.target_edit):
                w.clear()
        finally:
            self._loading = False


class _Plugin:
    def __init__(self):
        self.panel = LiveryPanel()
        self.dock = sp_ui.add_dock_widget(self.panel)
        self.dock.setWindowTitle("%s %s" % (TITLE, VERSION))
        self.dock.setMinimumWidth(220)
        QtCore.QTimer.singleShot(1500, self._ensure_visible)
        d = sp_event.DISPATCHER
        d.connect_strong(sp_event.ProjectEditionEntered, self.panel.on_project_opened)
        d.connect_strong(sp_event.ProjectAboutToClose, self.panel.on_project_closed)
        d.connect_strong(sp_event.ProjectAboutToSave, self._on_save)
        try:
            if sp_project.is_open():
                self.panel.on_project_opened()
        except Exception:  # noqa: BLE001
            pass

    def _ensure_visible(self):
        try:
            dock = self.dock
            app = QtWidgets.QApplication.instance()
            if dock.isFloating() and not any(
                    sc.availableGeometry().intersects(dock.frameGeometry()) for sc in app.screens()):
                dock.setFloating(False)
            if not dock.isVisible():
                dock.show()
        except Exception as e:  # noqa: BLE001
            _log("could not show the panel: %s" % e)

    def _on_save(self, *_):
        self.panel._save_global()
        self.panel._store_current()

    def close(self):
        self.panel._timer.stop()
        d = sp_event.DISPATCHER
        for evt, cb in ((sp_event.ProjectEditionEntered, self.panel.on_project_opened),
                        (sp_event.ProjectAboutToClose, self.panel.on_project_closed),
                        (sp_event.ProjectAboutToSave, self._on_save)):
            try:
                d.disconnect(evt, cb)
            except Exception:  # noqa: BLE001
                pass
        sp_ui.delete_ui_element(self.dock)


def start_plugin():
    global _PLUGIN
    _log("version %s loaded" % VERSION)
    _PLUGIN = _Plugin()


def close_plugin():
    global _PLUGIN
    if _PLUGIN is not None:
        _PLUGIN.close()
        _PLUGIN = None
