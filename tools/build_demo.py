"""Assemble only public demo files into _site (no server or private game logs)."""
from pathlib import Path
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]


def build(output=None):
    output = output or ROOT / "_site"
    output.mkdir(parents=True, exist_ok=True)
    for path in (ROOT / "demo").glob("*"):
        if path.is_file() and path.suffix in (".html", ".css", ".js", ".mjs"):
            shutil.copy2(path, output / path.name)
    data = output / "data"
    data.mkdir(exist_ok=True)
    # Copy only manifest-selected games and the fixed comparison datasets.
    manifest = json.loads((ROOT / "demo/data/manifest.json").read_text(encoding="utf-8"))
    names = ["manifest.json"] + [g["id"] + ".json" for g in manifest]
    names += [f"records-{a}-{b}.json" for a in ("all", "on", "off") for b in ("all", "on", "off")]
    for name in names:
        shutil.copy2(ROOT / "demo/data" / name, data / name)
    for directory in ("css", "vendor"):
        shutil.copytree(ROOT / "web" / directory, output / "static" / directory, dirs_exist_ok=True)
    js = output / "static/js"
    js.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "web/js/view.js", js / "view.js")
    # Reuse the record presentation unchanged, substituting its data transport.
    record = (ROOT / "web/js/record.js").read_text(encoding="utf-8")
    record = record.replace("`/api/records?legal_moves=${filter.value}&think_aloud=${filterTa.value}`", "`data/records-${filter.value}-${filterTa.value}.json`")
    record = record.replace("load();", "load().catch(showError);")
    record = record.replace("filter.onchange = load;", "filter.onchange = () => load().catch(showError);")
    record = record.replace("filterTa.onchange = load;", "filterTa.onchange = () => load().catch(showError);")
    record += '\nfunction showError() { document.querySelector("#agents").textContent = "Could not load records. Please reload to try again."; }\n'
    (js / "record.js").write_text(record, encoding="utf-8")
    (output / ".nojekyll").touch()
    shutil.copy2(ROOT / "LICENSE", output / "LICENSE")
    print(f"Built demo at {output}")


if __name__ == "__main__":
    build()
