#!/usr/bin/env python3
"""Assemble the local SpecRelab site from immutable source commits."""
from pathlib import Path
import argparse, json, re, shutil, subprocess, sys
import os
import tempfile
from urllib.parse import urlsplit
ROOT = Path(__file__).resolve().parents[1]
PINS = json.loads((ROOT / "source-revisions.json").read_text())
CACHE, WORK = ROOT / ".cache", ROOT / ".build-src"
BUILD, OUT = ROOT / ".build", ROOT / ".build/site"

def run(args, cwd=None, env=None):
    subprocess.run([str(x) for x in args], cwd=cwd, check=True, env=env)

def git_environment(offline):
    env=os.environ.copy()
    if offline: env["GIT_NO_LAZY_FETCH"]="1"
    return env

def cached_tree_status(dest, name, offline):
    env=git_environment(offline)
    try:
        subprocess.run([sys.executable,str(ROOT/"scripts/cache_integrity.py"),name,str(dest)],check=True,env=env)
    except subprocess.CalledProcessError as exc:
        if exc.returncode==2 and offline:
            raise SystemExit(f"Offline {name} cache cannot be integrity-checked without fetching missing objects; run scripts/fetch-sources.sh while online.") from exc
        raise SystemExit(1) from exc

def checkout(name, offline):
    spec=PINS["sources"][name]; dest=CACHE/name
    existed=dest.exists()
    if not existed:
        if offline: raise SystemExit(f"Offline build lacks pinned {name} source cache; run scripts/fetch-sources.sh")
        dest.parent.mkdir(parents=True,exist_ok=True)
        run(["git","clone","--filter=blob:none","--sparse","--no-checkout",spec["repository"],dest])
    if existed: cached_tree_status(dest,name,offline)
    if offline:
        try:
            run(["git","-C",dest,"cat-file","-e",spec["commit"]+"^{commit}"],env=git_environment(True))
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"Offline {name} cache lacks pinned commit {spec['commit']}; fetch it with scripts/fetch-sources.sh while online.") from exc
    else:
        run(["git","-C",dest,"fetch","--filter=blob:none","--no-tags","origin",spec["commit"]])
    paths={"gplspec":["_config.yml","_layouts","_includes","_tutorials","_benchmarks","assets","benchfigures","benchmark.md","api.md","getting-started.md","index.md","LICENSE","work/plots.md"],"DSpecM1D":["docs","DSpecM1D","tutorials/t1.cpp","README.md","data/params/first_seismogram.txt","love_numbers/report/love_numbers_report.pdf"],"YSpecpp":[".gitignore","CMakeLists.txt","README.md","LICENSE","YSpec","docs/site","docs/tools","docs/Doxyfile.in","docs/theory/adjoint-kernels.pdf","examples/prem.200","examples/cross-section/snapshot.json","examples/depths/input.json","examples/force/input.json","examples/gravity/cowling.json","examples/gravity/full.json","examples/gravity/none.json","examples/instrument/input.json","examples/kernels-3d/s-wave.json","examples/kernels-gallery/diffracted.json","examples/kernels-gallery/gallery.json","examples/kernels-gallery/make_input.py","examples/kernels/traveltimes.json","examples/map/snapshot.json","examples/record-section/input.json","python/yspec/plot"]}[name]
    directories={"_layouts","_includes","_tutorials","_benchmarks","assets","benchfigures","docs","docs/site","docs/tools","DSpecM1D","YSpec","python/yspec/plot"}
    patterns=["/"+path+("/" if path in directories else "") for path in paths]
    run(["git","-C",dest,"sparse-checkout","set","--no-cone","--",*patterns],env=git_environment(offline))
    run(["git","-C",dest,"checkout","--detach",spec["commit"]],env=git_environment(offline))
    got=subprocess.check_output(["git","-C",dest,"rev-parse","HEAD"],text=True).strip()
    if got != spec["commit"]: raise SystemExit(f"Pinned {name} source mismatch: {got}")
    cached_tree_status(dest,name,offline)
    return dest

def copy(src,dst):
    shutil.copytree(src,dst,dirs_exist_ok=True,ignore=shutil.ignore_patterns(".git","build","modeldata","vendor"))

def markdown_prose(text, transform):
    """Apply URL adapters outside fenced examples so code remains source-exact."""
    chunks=re.split(r"(?ms)(^```[^\n]*\n.*?^```|^~~~[^\n]*\n.*?^~~~)",text)
    return "".join(chunk if i%2 else transform(chunk) for i,chunk in enumerate(chunks))

def build_yspecpp(source_cache, commit):
    # Keep the temporary Git worktree outside Jekyll's source and clean it even
    # when a manual/API build fails; only checked output reaches the site.
    with tempfile.TemporaryDirectory(prefix="yspecpp-docs-",dir=BUILD) as scratch:
        source=Path(scratch)/"source"
        run(["git","clone","--shared","--no-checkout",source_cache,source])
        sparse=["/.gitignore","/CMakeLists.txt","/README.md","/LICENSE","/YSpec/",
                "/docs/site/","/docs/tools/","/docs/Doxyfile.in",
                "/docs/theory/adjoint-kernels.pdf","/examples/prem.200",
                "/examples/cross-section/snapshot.json",
                "/examples/depths/input.json",
                "/examples/force/input.json",
                "/examples/gravity/cowling.json",
                "/examples/gravity/full.json",
                "/examples/gravity/none.json",
                "/examples/instrument/input.json",
                "/examples/kernels-3d/s-wave.json",
                "/examples/kernels-gallery/diffracted.json",
                "/examples/kernels-gallery/gallery.json",
                "/examples/kernels-gallery/make_input.py",
                "/examples/kernels/traveltimes.json",
                "/examples/map/snapshot.json",
                "/examples/record-section/input.json",
                "/python/yspec/plot/"]
        run(["git","-C",source,"sparse-checkout","set","--no-cone","--",*sparse])
        actual=subprocess.check_output(["git","-C",source,"rev-parse","HEAD"],text=True).strip()
        if actual!=commit: raise SystemExit(f"YSpecpp build checkout mismatch: {actual}")
        # A sparse source cache intentionally lacks unrelated blobs. Populate
        # just the allowlisted index/worktree entries at its already pinned
        # HEAD, marking omitted files skipped without expanding/fetching them.
        run(["git","-C",source,"read-tree","--reset","-u",commit])
        dirty=subprocess.check_output(["git","status","--porcelain","--untracked-files=all"],cwd=source,text=True).strip()
        if dirty: raise SystemExit(f"YSpecpp isolated docs checkout is not clean: {dirty}")
        build_dir=source/"build/spec-relab-docs"
        run([sys.executable,source/"docs/tools/build_docs.py","--output",build_dir],cwd=source)
        manual=build_dir/"site"
        run([sys.executable,source/"docs/tools/check_docs.py",manual],cwd=source)
        target=WORK/"yspecpp"
        shutil.copytree(manual,target)
        shutil.copy2(source/"LICENSE",target/"LICENSE")
    return target

def suite_header(baseurl, current):
    items=[("All software",f"{baseurl}/#software",None),
           ("GPLSpec",f"{baseurl}/#gplspec","gplspec"),
           ("DSpecM1D",f"{baseurl}/dspecm1d/","dspecm1d"),
           ("YSpecpp",f"{baseurl}/yspecpp/","yspecpp"),
           ("DSpecM3D",f"{baseurl}/#dspecm3d",None)]
    links="".join(f'<a href="{href}"'+(f' class="current" aria-current="location"' if name==current else "")+f'>{label}</a>' for label,href,name in items)
    mark='<svg aria-hidden="true" class="brand-mark" viewBox="0 0 32 32"><circle cx="16" cy="16" r="13" fill="none" stroke="#d5a66c" stroke-width="1.5"/><path d="M4 16h24M16 3c5 6 5 20 0 26M16 3c-5 6-5 20 0 26" fill="none" stroke="#a9d9e4"/><circle cx="16" cy="16" r="3" fill="#d5a66c"/></svg>'
    return f'<header class="suite-header"><a class="suite-logo" href="{baseurl}/">{mark}<span>SpecRelab <span class="suite-context">Particle relabelling · radial spectral elements · spherical harmonics</span></span></a><nav aria-label="Project navigation" class="suite-nav">{links}</nav></header>'

def add_yspecpp_overlay(path, baseurl):
    page=path.read_text(errors="replace")
    if "<head>" not in page:
        raise SystemExit(f"YSpecpp page lacks a standard head: {path}")
    stylesheet=f'<link rel="stylesheet" href="{baseurl}/assets/hub.css">'
    script=f'<script defer src="{baseurl}/assets/js/code-copy.js"></script>' if "/api/" in path.as_posix() else ""
    page=page.replace("<head>","<head>"+stylesheet+script,1)
    body=re.search(r'<body\b([^>]*)>',page)
    if not body: raise SystemExit(f"YSpecpp page lacks a standard body: {path}")
    attrs=body.group(1)
    classes=re.search(r"\bclass=(['\"])(.*?)\1",attrs)
    if classes:
        value=classes.group(2)
        if "yspecpp" not in value.split():
            attrs=attrs[:classes.start(2)]+value+" yspecpp"+attrs[classes.end(2):]
    else:
        attrs+=' class="yspecpp"'
    page=page[:body.start()]+"<body"+attrs+">"+suite_header(baseurl,"yspecpp")+page[body.end():]
    path.write_text(page)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--baseurl",default="",help="Empty for domain root or /SpecRelab for project prefix")
    ap.add_argument("--offline",action="store_true",help="Require exact source commits in .cache")
    ap.add_argument("--doxygen-have-dot",choices=("auto","yes","no"),default="auto",help="Choose Doxygen Graphviz support; use no for stable runner output")
    ap.add_argument("--destination",type=Path,default=OUT,help="Build output directory (use a separate path for parallel root/prefix checks)")
    a=ap.parse_args()
    if a.baseurl not in ("","/SpecRelab"): raise SystemExit("--baseurl must be empty or /SpecRelab")
    for t in ("git","ruby","doxygen"):
        if not shutil.which(t): raise SystemExit(f"Required tool unavailable: {t}")
    gpl=checkout("gplspec",a.offline); dspec=checkout("DSpecM1D",a.offline); yspec=checkout("YSpecpp",a.offline)
    destination=a.destination if a.destination.is_absolute() else ROOT/a.destination
    global WORK
    BUILD.mkdir(parents=True,exist_ok=True)
    site_source=tempfile.TemporaryDirectory(prefix="specrelab-site-source-",dir=BUILD)
    WORK=Path(site_source.name)
    # Allowlisted web material only: no modeldata, builds, numerical sources or test trees.
    for name in ("_config.yml","_layouts","_includes","_tutorials","_benchmarks","assets","benchfigures","benchmark.md","api.md","getting-started.md","index.md","LICENSE","work/plots.md"):
        src=gpl/name
        if src.exists():
            dst=WORK/name
            if src.is_dir(): copy(src,dst)
            else: dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
    # Put every GPLSpec route beneath its own site namespace.
    cfg=(WORK/"_config.yml").read_text().replace('baseurl: "/gplspec"',f'baseurl: "{a.baseurl}"').replace('url: "https://adcm2.github.io"','url: ""').replace('permalink: /:collection/:name/','permalink: /gplspec/:collection/:name/')
    cfg += '\npermalink: /gplspec/:categories/:title:output_ext\n'
    cfg += f'\nhub_baseurl: "{a.baseurl}"\nexclude: [src, build, CMakeLists.txt, Doxyfile, README.md, Gemfile, Gemfile.lock, vendor, modeldata]\n'
    (WORK/"_config.yml").write_text(cfg)
    # The legacy Jekyll layout assumed its own `/gplspec` baseurl. The new
    # shared site owns the project prefix, so scope its local assets/routes.
    for rel in ("_layouts/default.html", "_includes/navigation.html"):
        f=WORK/rel; text=f.read_text()
        for path in ("assets/css/style.css", "assets/js/navigation.js", "getting-started/", "benchmark/", "api/"):
            text=text.replace("'/"+path+"'", "'/gplspec/"+path+"'")
        if rel == "_layouts/default.html":
            sidebar_home = "<h1><a href=\"{{ '/' | relative_url }}\">"
            sidebar_gpl = "<h1><a href=\"{{ '/gplspec/' | relative_url }}\">"
            if text.count(sidebar_home) != 1:
                raise SystemExit("Could not uniquely identify the GPLSpec sidebar logo link.")
            text=text.replace(sidebar_home,sidebar_gpl,1)
            polyfill='<script src="https://polyfill.io/v3/polyfill.min.js?features=es6"></script>'
            if text.count(polyfill) != 1:
                raise SystemExit("Could not uniquely identify the obsolete GPLSpec polyfill tag.")
            text=text.replace(polyfill,"",1)
            fallback='<noscript><style>.nav-toggle { display: none !important; } .nav-links { max-height: none !important; overflow: visible !important; }</style></noscript>'
            if text.count("</head>") != 1:
                raise SystemExit("Could not place the GPLSpec no-JavaScript navigation fallback.")
            text=text.replace("</head>",fallback+"\n</head>",1)
        f.write_text(text)
    # Make the generated GPLSpec navigation toggles native keyboard controls.
    toggle_pattern=re.compile(r'<h3 class="nav-toggle" data-target="([^"]+)">(.*?)</h3>',re.S)
    for rel in ("_includes/navigation.html",):
        f=WORK/rel; text=f.read_text()
        def toggle_button(match):
            target=match.group(1)
            return f'<button type="button" class="nav-toggle" data-target="nav-{target}" aria-expanded="false" aria-controls="nav-{target}">{match.group(2)}</button>'
        text,count=toggle_pattern.subn(toggle_button,text)
        if count != 4:
            raise SystemExit(f"Expected four GPLSpec navigation toggles in {rel}, found {count}.")
        panel_pattern=re.compile(r'(<ul\b[^>]*class="nav-links"[^>]*\bid=")([^"]+)(")')
        text,panel_count=panel_pattern.subn(lambda match: match.group(1)+"nav-"+match.group(2)+match.group(3),text)
        if panel_count != 4:
            raise SystemExit(f"Expected four GPLSpec navigation panels in {rel}, found {panel_count}.")
        f.write_text(text)
    nav_script=WORK/"assets/js/navigation.js"; script=nav_script.read_text()
    old_toggle='      this.classList.toggle("expanded");\n      navLinks.classList.toggle("expanded");\n'
    new_toggle='      const expanded = this.classList.toggle("expanded");\n      navLinks.classList.toggle("expanded", expanded);\n      this.setAttribute("aria-expanded", String(expanded));\n'
    if script.count(old_toggle) != 1: raise SystemExit("Could not uniquely identify GPLSpec navigation toggle handler.")
    script=script.replace(old_toggle,new_toggle,1)
    auto_expand='      toggle.classList.add("expanded");\n      navLinksContainer.classList.add("expanded");\n'
    auto_expanded='      toggle.classList.add("expanded");\n      toggle.setAttribute("aria-expanded", "true");\n      navLinksContainer.classList.add("expanded");\n'
    if script.count(auto_expand) != 1: raise SystemExit("Could not uniquely identify GPLSpec current-page expansion handler.")
    nav_script.write_text(script.replace(auto_expand,auto_expanded,1))
    style=WORK/"assets/css/style.css"; css=style.read_text()
    if css.count(".nav-section h3 {") != 1: raise SystemExit("Could not identify GPLSpec navigation heading styles.")
    css=css.replace(".nav-section h3 {", ".nav-section .nav-toggle {",1)
    button_styles='.nav-toggle {\n    cursor: pointer;'
    button_styles_new='.nav-toggle {\n    appearance: none;\n    font-family: inherit;\n    line-height: inherit;\n    cursor: pointer;'
    if css.count(button_styles) != 1: raise SystemExit("Could not identify GPLSpec toggle button styles.")
    style.write_text(css.replace(button_styles,button_styles_new,1))
    for f in WORK.rglob("*.md"):
        text=f.read_text()
        f.write_text(markdown_prose(text,lambda value:value.replace("'/tutorials/", "'/gplspec/tutorials/").replace("'/benchmark/", "'/gplspec/benchmark/")))
    # Source-pinned onboarding notice: the source pages remain unchanged.
    gpl_getting=WORK/"getting-started.md"
    gpl_pin=PINS["sources"]["gplspec"]["commit"]
    gpl_notice=(f"\n\n> **Revision for these examples:** [gplspec `{gpl_pin}`](https://github.com/adcm2/gplspec/commit/{gpl_pin}). `git clone` checks out the repository's default branch. After cloning, run `git checkout {gpl_pin}` from the checkout root before following the build examples below.\n\n")
    if gpl_getting.is_file():
        text=gpl_getting.read_text(); marker="Use these commands to configure and build the checkout at its current revision."
        if marker not in text: raise SystemExit("Could not place pinned-revision notice in GPLSpec getting-started source.")
        checkout_block=f"git clone https://github.com/adcm2/gplspec.git\ncd gplspec\ngit checkout {gpl_pin}"
        placeholder="cd /path/to/current/gplspec-checkout"
        if checkout_block not in text:
            if placeholder not in text: raise SystemExit("Could not place runnable pinned clone commands in GPLSpec setup block.")
            text=text.replace(placeholder,checkout_block,1)
        gpl_getting.write_text(text.replace(marker,gpl_notice+marker,1))
    # Explicit routes for source pages with front-matter permalinks.
    for filename,route in (("api.md","/gplspec/api/"),("benchmark.md","/gplspec/benchmark/"),("getting-started.md","/gplspec/getting-started/"),("index.md","/gplspec/")):
        f=WORK/filename
        if f.exists():
            s=f.read_text()
            if s.startswith("---"):
                end=s.find("\n---",3)
                if end>=0:
                    fm=s[3:end]
                    fm=re.sub(r"(?m)^permalink:.*\n?", "", fm)
                    fm += f"\npermalink: {route}"
                    s="---"+fm+s[end:]
            f.write_text(s)
    # Add the source-owned bundle plotting guide and tutorials landing route.
    plot_src=gpl/"work/plots.md"
    if plot_src.is_file():
        plot_dst=WORK/"work/plots.md"; plot_dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(plot_src,plot_dst)
        text=plot_dst.read_text()
        if text.startswith("---"):
            end=text.find("\n---",3)
            if end>=0: text="---\npermalink: /gplspec/work/plots/\n---"+text[end+4:]
        else: text="---\nlayout: default\ntitle: Field-bundle plotting guide\npermalink: /gplspec/work/plots/\n---\n\n"+text
        plot_dst.write_text(text)
    (WORK/"tutorials.md").write_text("---\nlayout: default\ntitle: GPLSpec tutorials\npermalink: /gplspec/tutorials/\n---\n\n# GPLSpec tutorials\n\n{% for tutorial in site.tutorials %}\n- [{{ tutorial.title | default: tutorial.name }}]({{ tutorial.url | relative_url }})\n{% endfor %}\n")
    (WORK/"benchmarks.md").write_text("---\nlayout: default\ntitle: GPLSpec benchmarks\npermalink: /gplspec/benchmarks/\n---\n\n# GPLSpec benchmarks\n\n{% for benchmark in site.benchmarks %}\n- [{{ benchmark.title | default: benchmark.name }}]({{ benchmark.url | relative_url }})\n{% endfor %}\n")
    # Preserve the complete legacy GPLSpec asset and license paths under its namespace.
    for rel in ("assets", "benchfigures"):
        copy(gpl/rel,WORK/"gplspec"/rel)
    # The namespace keeps pristine assets except for the generated navigation overlays.
    for rel in ("assets/css/style.css", "assets/js/navigation.js"):
        shutil.copy2(WORK/rel,WORK/"gplspec"/rel)
    (WORK/"gplspec"/"LICENSE").parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(gpl/"LICENSE",WORK/"gplspec"/"LICENSE")
    (WORK/"gplspec"/"robots.txt").write_text("User-agent: *\nAllow: /\n")
    # Preserve modeldata endpoints only in actual URL attributes. Literal
    # filenames in code examples remain exact.
    for f in list(WORK.rglob("*.md"))+list(WORK.rglob("*.html")):
        text=f.read_text(errors="ignore")
        def adapt_downloads(value):
            return re.sub(r"((?:href|src)=[\"'])([^\"']*modeldata/[^\"']*)([\"'])",
                          lambda m:m.group(1)+(m.group(2) if m.group(2).startswith(("http:","https:")) else "https://adcm2.github.io/gplspec/"+m.group(2).lstrip("./"))+m.group(3),value,flags=re.I)
        if f.suffix==".md": text=markdown_prose(text,adapt_downloads)
        else: text=adapt_downloads(text)
        f.write_text(text)
    # Convert links to published pages and immutable pinned source links.
    import urllib.parse
    commit=PINS["sources"]["gplspec"]["commit"]
    published={"getting-started.md":"/gplspec/getting-started/","benchmark.md":"/gplspec/benchmark/","api.md":"/gplspec/api/","index.md":"/gplspec/","tutorials.md":"/gplspec/tutorials/","work/plots.md":"/gplspec/work/plots/"}
    def adapt_link(match):
        target=match.group(1).strip()
        if target.startswith(("http:","https:","mailto:","#","{{")): return match.group(0)
        path,sep,fragment=target.partition("#")
        path=urllib.parse.unquote(path)
        if path in published:
            url="{{ '"+published[path]+"' | relative_url }}"
        elif path.lstrip("./").startswith("modeldata/"):
            url="https://adcm2.github.io/gplspec/"+path.lstrip("./")
        elif path=="tutorials.md":
            url="{{ '/gplspec/tutorials/' | relative_url }}"
        elif path:
            url=f"https://github.com/adcm2/gplspec/blob/{commit}/{path}"
        else: return match.group(0)
        if sep: url += "#"+fragment
        return "]("+url+")"
    for f in WORK.rglob("*.md"):
        original=f.read_text()
        f.write_text(markdown_prose(original,lambda value:re.sub(r"\]\(([^)]+)\)",adapt_link,value)))
    # Literal C++ initializer braces are text, not Liquid; shield only affected code blocks.
    for f in WORK.rglob("*.md"):
        s=f.read_text()
        def shield(match):
            block=match.group(0)
            return "{% raw %}\n"+block+"\n{% endraw %}" if "{{" in block or "{%" in block else block
        f.write_text(re.sub(r"(?ms)^```[^\n]*\n.*?^```",shield,s))
    # Shared navigation over the source-preserved GPLSpec layout.
    layout=WORK/"_layouts/default.html"
    html=layout.read_text()
    bar='''<header class="sitebar"><a class="brand" href="{{ site.hub_baseurl }}/">SpecRelab</a><nav aria-label="SpecRelab"><a href="{{ site.hub_baseurl }}/">Home</a><a href="{{ site.hub_baseurl }}/gplspec/">GPLSpec</a><a href="{{ site.hub_baseurl }}/dspecm1d/">DSpecM1D</a><a href="{{ site.hub_baseurl }}/yspecpp/">YSpecpp</a><a href="{{ site.hub_baseurl }}/#dspecm3d">DSpecM3D</a></nav></header>\n<link rel="stylesheet" href="{{ site.hub_baseurl }}/assets/css/hub.css">\n<script defer src="{{ site.hub_baseurl }}/assets/js/code-copy.js"></script>\n'''
    html=html.replace("<body>","<body>\n"+bar); layout.write_text(html)
    # DSpecM1D authored pages, example input and published report.
    droot=WORK/"dspecm1d"; shutil.copytree(dspec/"docs/site",droot,dirs_exist_ok=True)
    (droot/"data/params").mkdir(parents=True,exist_ok=True)
    (droot/"reports").mkdir(parents=True,exist_ok=True)
    shutil.copy2(dspec/"data/params/first_seismogram.txt",droot/"data/params/first_seismogram.txt")
    shutil.copy2(dspec/"love_numbers/report/love_numbers_report.pdf",droot/"reports/love_numbers_report.pdf")
    # The pinned DSpecM1D script also injects a legacy text copy button. Keep
    # its active-page navigation behavior while delegating all code copying to
    # the shared, accessible website control.
    legacy_js=droot/"assets/site.js"
    legacy_script=legacy_js.read_text()
    legacy_copy=re.compile(r'\n  document\.querySelectorAll\("pre"\)\.forEach\(\(pre\) => \{.*?\n  \}\);\n(?=\}\)\(\);)',re.S)
    legacy_script,legacy_count=legacy_copy.subn("\n",legacy_script)
    if legacy_count!=1 or 'link.setAttribute("aria-current", "page")' not in legacy_script or 'querySelectorAll("pre")' in legacy_script:
        raise SystemExit("Could not safely disable only the legacy DSpecM1D code-copy handler")
    legacy_js.write_text(legacy_script)
    inst=droot/"installation.html"; s=inst.read_text()
    s=s.replace('<section class="page-section"><h2>Requirements</h2>','<section class="page-section" id="quick-start"><h2>Requirements</h2>',1)
    dspec_pin=PINS["sources"]["DSpecM1D"]["commit"]
    dspec_notice=(f'<p class="pin-guidance"><strong>Revision for these examples:</strong> <a href="https://github.com/adcm2/DSpecM1D/commit/{dspec_pin}"><code>{dspec_pin}</code></a>. Cloning checks out the default branch. After cloning and entering <code>DSpecM1D</code>, run <code>git checkout {dspec_pin}</code> before continuing with any build example on this page.</p>')
    marker='</header>\n<section class="page-section"'
    if marker not in s: raise SystemExit("Could not place pinned-revision notice on DSpecM1D installation page.")
    s=s.replace(marker,'</header>\n'+dspec_notice+'\n<section class="page-section"',1)
    checkout_line='cd DSpecM1D\ngit checkout '+dspec_pin
    if checkout_line not in s:
        clone_line='cd DSpecM1D'
        if clone_line not in s: raise SystemExit("Could not place pinned checkout command in DSpecM1D installation block.")
        s=s.replace(clone_line,checkout_line,1)
    inst.write_text(s)
    library=droot/"library-example.html"; library_text=library.read_text()
    source_tree_marker='<section class="page-section"><h2>Build and run in the source tree</h2>'
    if source_tree_marker not in library_text: raise SystemExit("Could not identify DSpecM1D source-tree C++ example.")
    library_text=library_text.replace(source_tree_marker,'<section class="page-section" id="source-tree-example"><h2>Build and run in the source tree</h2>',1)
    library.write_text(library_text)
    first=droot/"first-seismogram.html"; first_text=first.read_text()
    first_notice=(f'<p class="pin-guidance"><strong>Revision for this example:</strong> <a href="https://github.com/adcm2/DSpecM1D/commit/{dspec_pin}"><code>{dspec_pin}</code></a>. Cloning checks out the default branch. After <code>git clone</code> and <code>cd DSpecM1D</code>, run <code>git checkout {dspec_pin}</code> from the checkout root before continuing with the build commands below.</p>')
    first_marker='<section class="page-section"><h2>2. Clone and build</h2>'
    if first_marker not in first_text: raise SystemExit("Could not place pinned-revision notice on DSpecM1D first-seismogram page.")
    if 'cd DSpecM1D\ngit checkout '+dspec_pin not in first_text:
        clone_line='cd DSpecM1D'
        if clone_line not in first_text: raise SystemExit("Could not place pinned checkout command in DSpecM1D first-seismogram block.")
        first_text=first_text.replace(clone_line,clone_line+'\ngit checkout '+dspec_pin,1)
    first.write_text(first_text.replace(first_marker,first_marker+first_notice,1))
    suite=suite_header(a.baseurl,"dspecm1d")
    nav_group=re.compile(r'<li class="nav-group">(.*?)(</li>)',re.S)
    nav_link=re.compile(r'<a\b[^>]*>.*?</a>',re.S)
    for page in droot.rglob("*.html"):
        if "api" in page.relative_to(droot).parts: continue
        html=page.read_text(errors="replace")
        def format_nav_group(match):
            content=match.group(1).strip()
            links=nav_link.findall(content)
            label_text=nav_link.sub("",content).strip()
            if not links or not label_text: raise SystemExit(f"Unexpected DSpec navigation group in {page}")
            items="".join(f"<li>{item}</li>" for item in links)
            return f'<li class="nav-group"><span class="nav-group-label">{label_text}</span><ul class="nav-group-links">{items}</ul></li>'
        html,group_count=nav_group.subn(format_nav_group,html)
        if group_count not in (0,4): raise SystemExit(f"Expected four DSpec navigation groups in {page}, found {group_count}")
        if "<head>" in html:
            html=html.replace("<head>","<head><link rel=\"stylesheet\" href=\""+a.baseurl+"/assets/hub.css\">",1)
        if "<body>" in html:
            html=html.replace("<body>","<body class=\"dspecm1d\">"+suite,1)
        script_tag='<script defer src="'+a.baseurl+'/assets/js/code-copy.js"></script>'
        if "assets/js/code-copy.js" not in html and "<head>" in html:
            html=html.replace("<head>","<head>"+script_tag,1)
        page.write_text(html)
    # Regenerate the full API directly from the pinned Doxygen input tree.
    doxy_work=tempfile.TemporaryDirectory(prefix="dspecm1d-doxygen-",dir=BUILD)
    doxyout=Path(doxy_work.name)/"output"; doxyout.mkdir()
    doxyfile=Path(doxy_work.name)/"Doxyfile"
    have_dot={"yes":"YES","no":"NO"}.get(a.doxygen_have_dot,"YES" if shutil.which("dot") else "NO")
    if a.doxygen_have_dot=="yes" and not shutil.which("dot"):
        raise SystemExit("--doxygen-have-dot yes requires Graphviz dot on PATH")
    doxyfile.write_text(f'''PROJECT_NAME = DSpecM1D\nPROJECT_NUMBER = 0.1.0\nOUTPUT_DIRECTORY = {doxyout}\nCREATE_SUBDIRS = NO\nOUTPUT_LANGUAGE = English\nGENERATE_HTML = YES\nGENERATE_LATEX = NO\nRECURSIVE = YES\nEXTRACT_ALL = YES\nEXTRACT_PRIVATE = NO\nEXTRACT_STATIC = YES\nQUIET = YES\nWARN_IF_UNDOCUMENTED = YES\nWARN_IF_DOC_ERROR = YES\nHAVE_DOT = {have_dot}\nGENERATE_TREEVIEW = YES\nFULL_SIDEBAR = YES\nSTRIP_FROM_PATH = .\nUSE_MDFILE_AS_MAINPAGE = README.md\nINPUT = README.md DSpecM1D tutorials/t1.cpp\nFILE_PATTERNS = *.h *.hpp *.cpp *.md\nEXCLUDE_PATTERNS = */DSpecM1D/src/BiCGSTABT.h\n''')
    run(["doxygen",doxyfile],cwd=dspec)
    api=doxyout/"html"
    if not (api/"index.html").is_file() or not (api/"search/search.js").is_file(): raise SystemExit("Doxygen output incomplete: missing index/search")
    shutil.copytree(api,droot/"api",dirs_exist_ok=True)
    # Doxygen's README guide links still target the old standalone publisher.
    # Adapt only href attributes for manual pages that are copied into this
    # site; leave generated API URLs, source links, and body/code text intact.
    manual_pages={"first-seismogram.html","installation.html","library-example.html","parameter-files.html","library-testing.html"}
    manual_prefixes=("/DSpecM1D/","/SpecRelab/dspecm1d/")
    href_attribute=re.compile(r'''(?<![\w:-])(\bhref\s*=\s*)(["'])(.*?)\2''',re.I|re.S)
    def adapt_manual_href(match):
        value=match.group(3)
        parsed=urlsplit(value)
        prefix=next((prefix for prefix in manual_prefixes if parsed.path==prefix or parsed.path.startswith(prefix)),None)
        if parsed.scheme=="https" and parsed.netloc=="adcm2.github.io" and prefix:
            relative=parsed.path.removeprefix(prefix)
            if not relative or relative in manual_pages:
                value=f"{a.baseurl}/dspecm1d/"+relative
                if parsed.query:value+="?"+parsed.query
                if parsed.fragment:value+="#"+parsed.fragment
        return match.group(1)+match.group(2)+value+match.group(2)
    probe='<a href="https://adcm2.github.io/DSpecM1D/library-testing.html?view=full#tests">legacy-guide</a><a href="https://adcm2.github.io/SpecRelab/dspecm1d/installation.html?view=quick#quick-start">current-guide</a><a href="https://adcm2.github.io/SpecRelab/dspecm1d/">current-home</a><a href="https://adcm2.github.io/DSpecM1D/api/classFoo.html">legacy-API</a><a href="https://adcm2.github.io/SpecRelab/dspecm1d/api/classFoo.html">current-API</a><a href="https://other.example/DSpecM1D/installation.html">other</a><a data-href="https://adcm2.github.io/SpecRelab/dspecm1d/installation.html">data</a><svg xlink:href="https://adcm2.github.io/SpecRelab/dspecm1d/installation.html"></svg><p>https://adcm2.github.io/SpecRelab/dspecm1d/installation.html</p>'
    checked=href_attribute.sub(adapt_manual_href,probe)
    expected=f'<a href="{a.baseurl}/dspecm1d/library-testing.html?view=full#tests">legacy-guide</a><a href="{a.baseurl}/dspecm1d/installation.html?view=quick#quick-start">current-guide</a><a href="{a.baseurl}/dspecm1d/">current-home</a><a href="https://adcm2.github.io/DSpecM1D/api/classFoo.html">legacy-API</a><a href="https://adcm2.github.io/SpecRelab/dspecm1d/api/classFoo.html">current-API</a><a href="https://other.example/DSpecM1D/installation.html">other</a><a data-href="https://adcm2.github.io/SpecRelab/dspecm1d/installation.html">data</a><svg xlink:href="https://adcm2.github.io/SpecRelab/dspecm1d/installation.html"></svg><p>https://adcm2.github.io/SpecRelab/dspecm1d/installation.html</p>'
    if checked!=expected:raise SystemExit("Doxygen manual-link adapter regression: rewrote an unrelated URL or changed a non-href attribute")
    for page in (droot/"api").rglob("*.html"):
        html=page.read_text(errors="replace")
        html=href_attribute.sub(adapt_manual_href,html)
        script_tag='<script defer src="'+a.baseurl+'/assets/js/code-copy.js"></script>'
        if "assets/js/code-copy.js" not in html and "<head>" in html:
            html=html.replace("<head>","<head>"+script_tag,1)
        if "<body>" in html:
            html=html.replace("<body>","<body class=\"dspecm1d\">"+suite,1)
        if "<head>" in html:
            html=html.replace("<head>","<head><link rel=\"stylesheet\" href=\""+a.baseurl+"/assets/hub.css\">",1)
        page.write_text(html)
    yroot=build_yspecpp(yspec,PINS["sources"]["YSpecpp"]["commit"])
    for page in yroot.rglob("*.html"):
        add_yspecpp_overlay(page,a.baseurl)
    # Legacy GPLSpec API page adapts to the current API overview.
    alias=WORK/"legacy-api-alias.html"
    alias.write_text("---\npermalink: /gplspec/api/api.html\n---\n<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta http-equiv=\"refresh\" content=\"0; url={{ '/gplspec/api/' | relative_url }}\"><title>GPLSpec API</title><p><a href=\"{{ '/gplspec/api/' | relative_url }}\">Open the GPLSpec API overview</a></p></html>\n")
    # Shared homepage is authored from the approved Stage 1 preview template.
    template=ROOT/"assets/templates/index.html"
    if not template.is_file(): raise SystemExit("Missing approved homepage template assets/templates/index.html")
    shutil.copy2(template,WORK/"index.html")
    for rel in ("assets/images/dspecm1d-polar-rays.svg","assets/data/first_seismogram.out_t.out","assets/data/dspecm1d-paper-ex1-excerpt.txt","assets/hub.css"):
        src=ROOT/rel
        if not src.is_file(): raise SystemExit(f"Missing approved asset {rel}")
    shutil.copy2(ROOT/"assets/images/dspecm1d-polar-rays.svg",WORK/"assets/earth-structure.svg")
    shutil.copy2(ROOT/"assets/data/first_seismogram.out_t.out",WORK/"assets/first-seismogram.out_t.out")
    shutil.copy2(ROOT/"assets/data/dspecm1d-paper-ex1-excerpt.txt",WORK/"assets/dspecm1d-paper-ex1-excerpt.txt")
    shutil.copy2(ROOT/"assets/hub.css",WORK/"assets/hub.css")
    (WORK/"assets/css").mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/"assets/css/hub.css",WORK/"assets/css/hub.css")
    (WORK/"assets/js").mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/"assets/js/code-copy.js",WORK/"assets/js/code-copy.js")
    (WORK/"Gemfile").write_text((ROOT/"Gemfile").read_text()); (WORK/"Gemfile.lock").write_text((ROOT/"Gemfile.lock").read_text())
    shutil.rmtree(destination,ignore_errors=True); destination.parent.mkdir(parents=True,exist_ok=True)
    run([ROOT/"scripts/bundle","exec","jekyll","build","--source",WORK,"--destination",destination,"--baseurl",a.baseurl],cwd=ROOT)
    for name in ("feed.xml", "sitemap.xml"):
        generated=destination/name
        if generated.is_file():
            target=destination/"gplspec"/name; target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(generated,target)
    print(f"Built {destination} (baseurl={a.baseurl or 'domain root'})")
if __name__=="__main__": main()
