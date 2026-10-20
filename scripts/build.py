#!/usr/bin/env python3
"""Assemble the local SpecRelab site from immutable source commits."""
from pathlib import Path
import argparse, json, re, shutil, subprocess, sys
import os
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
    paths={"gplspec":["_config.yml","_layouts","_includes","_tutorials","_benchmarks","assets","benchfigures","benchmark.md","api.md","getting-started.md","index.md","LICENSE","work/plots.md"],"DSpecM1D":["docs","DSpecM1D","tutorials/t1.cpp","README.md","data/params/first_seismogram.txt","love_numbers/report/love_numbers_report.pdf"]}[name]
    directories={"_layouts","_includes","_tutorials","_benchmarks","assets","benchfigures","docs","DSpecM1D"}
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
    gpl=checkout("gplspec",a.offline); dspec=checkout("DSpecM1D",a.offline)
    destination=a.destination if a.destination.is_absolute() else ROOT/a.destination
    shutil.rmtree(WORK,ignore_errors=True); WORK.mkdir(parents=True)
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
    bar='''<header class="sitebar"><a class="brand" href="{{ site.hub_baseurl }}/">SpecRelab</a><nav aria-label="SpecRelab"><a href="{{ site.hub_baseurl }}/">Home</a><a href="{{ site.hub_baseurl }}/gplspec/">GPLSpec</a><a href="{{ site.hub_baseurl }}/dspecm1d/">DSpecM1D</a></nav></header>\n<link rel="stylesheet" href="{{ site.hub_baseurl }}/assets/css/hub.css">\n'''
    html=html.replace("<body>","<body>\n"+bar); layout.write_text(html)
    # DSpecM1D authored pages, example input and published report.
    droot=WORK/"dspecm1d"; shutil.copytree(dspec/"docs/site",droot,dirs_exist_ok=True)
    (droot/"data/params").mkdir(parents=True,exist_ok=True)
    (droot/"reports").mkdir(parents=True,exist_ok=True)
    shutil.copy2(dspec/"data/params/first_seismogram.txt",droot/"data/params/first_seismogram.txt")
    shutil.copy2(dspec/"love_numbers/report/love_numbers_report.pdf",droot/"reports/love_numbers_report.pdf")
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
    status_text='The installed-package path has a known Linux CI failure while its CMake install step is under investigation.'
    ci_link='<a href="https://github.com/adcm2/DSpecM1D/actions/runs/39327159316">CI run details</a>.'
    install_marker='<section class="page-section"><h2>Install the CMake package</h2>'
    if install_marker not in s: raise SystemExit("Could not place installed-package status in DSpecM1D installation guide.")
    install_note=f'<p class="pin-guidance"><strong>Package status:</strong> {status_text} Use the <a href="library-example.html#source-tree-example">in-tree C++ example</a> while this is investigated. {ci_link}</p>'
    s=s.replace(install_marker,install_note+install_marker,1)
    inst.write_text(s)
    library=droot/"library-example.html"; library_text=library.read_text()
    source_tree_marker='<section class="page-section"><h2>Build and run in the source tree</h2>'
    if source_tree_marker not in library_text: raise SystemExit("Could not identify DSpecM1D source-tree C++ example.")
    library_text=library_text.replace(source_tree_marker,'<section class="page-section" id="source-tree-example"><h2>Build and run in the source tree</h2>',1)
    consumer_marker='<section class="page-section"><h2>Install and build a downstream consumer</h2>'
    if consumer_marker not in library_text: raise SystemExit("Could not place installed-package status in DSpecM1D library example.")
    library_note=f'<p class="pin-guidance"><strong>Package status:</strong> {status_text} Use the <a href="#source-tree-example">in-tree C++ example</a> while this is investigated. {ci_link}</p>'
    library.write_text(library_text.replace(consumer_marker,library_note+consumer_marker,1))
    first=droot/"first-seismogram.html"; first_text=first.read_text()
    first_notice=(f'<p class="pin-guidance"><strong>Revision for this example:</strong> <a href="https://github.com/adcm2/DSpecM1D/commit/{dspec_pin}"><code>{dspec_pin}</code></a>. Cloning checks out the default branch. After <code>git clone</code> and <code>cd DSpecM1D</code>, run <code>git checkout {dspec_pin}</code> from the checkout root before continuing with the build commands below.</p>')
    first_marker='<section class="page-section"><h2>2. Clone and build</h2>'
    if first_marker not in first_text: raise SystemExit("Could not place pinned-revision notice on DSpecM1D first-seismogram page.")
    if 'cd DSpecM1D\ngit checkout '+dspec_pin not in first_text:
        clone_line='cd DSpecM1D'
        if clone_line not in first_text: raise SystemExit("Could not place pinned checkout command in DSpecM1D first-seismogram block.")
        first_text=first_text.replace(clone_line,clone_line+'\ngit checkout '+dspec_pin,1)
    first.write_text(first_text.replace(first_marker,first_marker+first_notice,1))
    suite='''<header class="suite-header"><a class="suite-logo" href="__BASE__/"><svg aria-hidden="true" class="brand-mark" viewBox="0 0 32 32"><circle cx="16" cy="16" r="13" fill="none" stroke="#d5a66c" stroke-width="1.5"/><path d="M4 16h24M16 3c5 6 5 20 0 26M16 3c-5 6-5 20 0 26" fill="none" stroke="#a9d9e4"/><circle cx="16" cy="16" r="3" fill="#d5a66c"/></svg><span>SpecRelab <span class="suite-context">Particle relabelling · radial spectral elements · spherical harmonics</span></span></a><nav aria-label="Project navigation" class="suite-nav"><a href="__BASE__/#software">All software</a><a href="__BASE__/#gplspec">GPLSpec</a><a class="current" aria-current="location" href="__BASE__/dspecm1d/">DSpecM1D</a><a href="__BASE__/#dspecm3d">DSpecM3D</a></nav></header>'''
    suite=suite.replace("__BASE__",a.baseurl)
    for page in droot.rglob("*.html"):
        if "api" in page.relative_to(droot).parts: continue
        html=page.read_text(errors="replace")
        if "<head>" in html:
            html=html.replace("<head>","<head><link rel=\"stylesheet\" href=\""+a.baseurl+"/assets/hub.css\">",1)
        if "<body>" in html:
            html=html.replace("<body>","<body>"+suite,1)
        page.write_text(html)
    # Regenerate the full API directly from the pinned Doxygen input tree.
    doxyout=BUILD/"doxygen"; shutil.rmtree(doxyout,ignore_errors=True); doxyout.mkdir(parents=True)
    doxyfile=BUILD/"Doxyfile"
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
    href_attribute=re.compile(r'''(?<![\w:-])(\bhref\s*=\s*)(["'])(.*?)\2''',re.I|re.S)
    def adapt_manual_href(match):
        value=match.group(3)
        parsed=urlsplit(value)
        if parsed.scheme=="https" and parsed.netloc=="adcm2.github.io" and (parsed.path=="/DSpecM1D/" or parsed.path.startswith("/DSpecM1D/")):
            relative=parsed.path.removeprefix("/DSpecM1D/")
            if not relative or relative in manual_pages:
                value=f"{a.baseurl}/dspecm1d/"+relative
                if parsed.query:value+="?"+parsed.query
                if parsed.fragment:value+="#"+parsed.fragment
        return match.group(1)+match.group(2)+value+match.group(2)
    probe='<a href="https://adcm2.github.io/DSpecM1D/library-testing.html?view=full#tests">guide</a><a href="https://adcm2.github.io/DSpecM1D/api/classFoo.html">API</a><a href="https://other.example/DSpecM1D/installation.html">other</a><a data-href="https://adcm2.github.io/DSpecM1D/installation.html">data</a><svg xlink:href="https://adcm2.github.io/DSpecM1D/installation.html"></svg><p>https://adcm2.github.io/DSpecM1D/installation.html</p>'
    checked=href_attribute.sub(adapt_manual_href,probe)
    expected=f'<a href="{a.baseurl}/dspecm1d/library-testing.html?view=full#tests">guide</a><a href="https://adcm2.github.io/DSpecM1D/api/classFoo.html">API</a><a href="https://other.example/DSpecM1D/installation.html">other</a><a data-href="https://adcm2.github.io/DSpecM1D/installation.html">data</a><svg xlink:href="https://adcm2.github.io/DSpecM1D/installation.html"></svg><p>https://adcm2.github.io/DSpecM1D/installation.html</p>'
    if checked!=expected:raise SystemExit("Doxygen manual-link adapter regression: rewrote an unrelated URL or changed a non-href attribute")
    for page in (droot/"api").rglob("*.html"):
        html=page.read_text(errors="replace")
        html=href_attribute.sub(adapt_manual_href,html)
        if "<head>" in html:
            html=html.replace("<head>","<head><link rel=\"stylesheet\" href=\""+a.baseurl+"/assets/hub.css\">",1)
        if "<body>" in html:
            html=html.replace("<body>","<body>"+suite,1)
        page.write_text(html)
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
    (WORK/"Gemfile").write_text((ROOT/"Gemfile").read_text()); (WORK/"Gemfile.lock").write_text((ROOT/"Gemfile.lock").read_text())
    shutil.rmtree(destination,ignore_errors=True); destination.parent.mkdir(parents=True,exist_ok=True)
    run([ROOT/"scripts/bundle","exec","jekyll","build","--source",WORK,"--destination",destination,"--baseurl",a.baseurl],cwd=ROOT)
    for name in ("feed.xml", "sitemap.xml"):
        generated=destination/name
        if generated.is_file():
            target=destination/"gplspec"/name; target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(generated,target)
    print(f"Built {destination} (baseurl={a.baseurl or 'domain root'})")
if __name__=="__main__": main()
