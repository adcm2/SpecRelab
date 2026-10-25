#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json, subprocess
from pathlib import Path
root=Path.cwd(); pins=json.loads((root/'source-revisions.json').read_text())
for name,item in pins['sources'].items():
    dest=root/'.cache'/name; dest.parent.mkdir(parents=True,exist_ok=True)
    existed=dest.exists()
    if not existed:
        subprocess.run(['git','clone','--filter=blob:none','--sparse','--no-checkout',item['repository'],str(dest)],check=True)
    else:
        subprocess.run(['python3',str(root/'scripts/cache_integrity.py'),name,str(dest)],check=True)
    subprocess.run(['git','-C',str(dest),'fetch','--filter=blob:none','--no-tags','origin',item['commit']],check=True)
    paths={'gplspec':['_config.yml','_layouts','_includes','_tutorials','_benchmarks','assets','benchfigures','benchmark.md','api.md','getting-started.md','index.md','LICENSE','work/plots.md'],'DSpecM1D':['docs','DSpecM1D','tutorials/t1.cpp','README.md','data/params/first_seismogram.txt','love_numbers/report/love_numbers_report.pdf'],'YSpecpp':['.gitignore','CMakeLists.txt','README.md','LICENSE','YSpec','docs/site','docs/tools','docs/Doxyfile.in','docs/theory/adjoint-kernels.pdf','examples/prem.200','examples/cross-section/snapshot.json','examples/depths/input.json','examples/force/input.json','examples/gravity/cowling.json','examples/gravity/full.json','examples/gravity/none.json','examples/instrument/input.json','examples/kernels-3d/s-wave.json','examples/kernels-gallery/diffracted.json','examples/kernels-gallery/gallery.json','examples/kernels-gallery/make_input.py','examples/kernels/traveltimes.json','examples/map/snapshot.json','examples/record-section/input.json','python/yspec/plot'] }[name]
    directories={'_layouts','_includes','_tutorials','_benchmarks','assets','benchfigures','docs','docs/site','docs/tools','DSpecM1D','YSpec','python/yspec/plot'}
    patterns=['/'+path+('/' if path in directories else '') for path in paths]
    subprocess.run(['git','-C',str(dest),'sparse-checkout','set','--no-cone','--',*patterns],check=True)
    subprocess.run(['git','-C',str(dest),'checkout','--detach',item['commit']],check=True)
    got=subprocess.check_output(['git','-C',str(dest),'rev-parse','HEAD'],text=True).strip()
    if got!=item['commit']: raise SystemExit(f'{name} pin mismatch: {got}')
    subprocess.run(['python3',str(root/'scripts/cache_integrity.py'),name,str(dest)],check=True)
PY
