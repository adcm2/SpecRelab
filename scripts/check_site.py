#!/usr/bin/env python3
"""Check local HTML links, anchors, frozen destinations, assets and provenance."""
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote
import argparse, hashlib, html, json, re, sys
ROOT=Path(__file__).resolve().parents[1]
class Page(HTMLParser):
    def __init__(self): super().__init__(); self.ids=set(); self.id_tags={}; self.id_counts={}; self.links=[]; self.meta={}; self.text=[]; self.buttons=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if 'id' in a:self.ids.add(a['id']); self.id_tags.setdefault(a['id'],set()).add(tag); self.id_counts[a['id']]=self.id_counts.get(a['id'],0)+1
        if tag=='a' and 'name' in a:self.ids.add(a['name'])
        if tag=='button':self.buttons.append(a)
        for attr in ('href','src','action','poster'):
            if a.get(attr):self.links.append((a[attr],tag,attr))
        if tag=='meta' and a.get('name'):self.meta[a['name'].lower()]=a.get('content','')
    def handle_data(self,data):self.text.append(data)
def candidate_for(site,page,path):
    path=unquote(path)
    if not path:return page
    if path.startswith('/'): path=path.lstrip('/')
    else: path=(page.parent.relative_to(site)/path).as_posix()
    target=(site/path).resolve()
    try: target.relative_to(site.resolve())
    except ValueError:return None
    if target.is_dir():target=target/'index.html'
    if not target.exists() and not Path(path).suffix:
        if (target/'index.html').exists():target=target/'index.html'
        elif target.with_suffix('.html').exists():target=target.with_suffix('.html')
    return target
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--site',type=Path,default=ROOT/'.build/site'); ap.add_argument('--baseurl',default='/SpecRelab')
    a=ap.parse_args(); site=a.site.resolve()
    if not (site/'index.html').is_file():raise SystemExit(f'Missing built site: {site}')
    files=list(site.rglob('*.html')); pages={}
    for f in files:
        p=Page(); p.feed(f.read_text(errors='replace')); pages[f.resolve()]=p
    known={}
    m=json.loads((ROOT/'compatibility-map.json').read_text())
    for item in m['known_preexisting_404s']:
        known[item.get('new_route') or item.get('legacy_target_path') or item.get('legacy_path')]=item
    errors=[]; checked=0
    for source,p in pages.items():
        for raw,tag,attr in p.links:
            u=urlsplit(raw)
            if u.scheme or u.netloc or raw.startswith(('data:','javascript:','mailto:','tel:')):continue
            path=u.path
            if a.baseurl and (path==a.baseurl or path.startswith(a.baseurl+'/')):path=path[len(a.baseurl):] or '/'
            target=candidate_for(site,source,path)
            if target is None: errors.append(f'{source.relative_to(site)}: unsafe local target {raw}');continue
            if not target.is_file():
                if raw in known or path in known or path.lstrip('/') in known:continue
                errors.append(f'{source.relative_to(site)}: missing {raw} -> {target.relative_to(site) if target.is_relative_to(site) else target}')
                continue
            checked+=1
            if u.fragment and target.suffix.lower() in ('.html','.htm'):
                dest=pages.get(target.resolve())
                if dest is None:
                    dest=Page(); dest.feed(target.read_text(errors='replace')); pages[target.resolve()]=dest
                if unquote(u.fragment) not in dest.ids and unquote(u.fragment) not in {'top','main'}:
                    errors.append(f'{source.relative_to(site)}: missing anchor {u.fragment} in {target.relative_to(site)}')
    # Every frozen route is accounted for; large GPL data remain remote by approved policy.
    missing_routes=[]
    for item in m['gplspec_routes']+m['dspecm1d_routes']:
        dest=item.get('new')
        if not dest:
            if 'modeldata/' in item.get('legacy_path','') and 'retain-legacy-download-endpoint' in item['action']:continue
            continue
        url=dest
        if not a.baseurl and url.startswith('/SpecRelab/'):
            url=url[len('/SpecRelab'):]
        if a.baseurl and (url==a.baseurl or url.startswith(a.baseurl+'/')):url=url[len(a.baseurl):] or '/'
        target=candidate_for(site,site/'index.html',url)
        if target is None or not target.is_file():missing_routes.append((item.get('legacy_path'),dest))
    if missing_routes:errors.extend(f'compatibility destination missing: {old} -> {new}' for old,new in missing_routes)
    # GPLSpec's own brand link stays inside its namespace; the suite header keeps its separate home links.
    base=a.baseurl
    expected_gpl_home=f'{base}/gplspec/' if base else '/gplspec/'
    expected_suite_home=f'{base}/' if base else '/'
    gpl_home_page=site/'gplspec/getting-started/index.html'
    gpl_home_html=gpl_home_page.read_text(errors='replace') if gpl_home_page.is_file() else ''
    sidebar=re.search(r'<div class="sidebar-header">\s*<h1><a href="([^"]+)"',gpl_home_html)
    if not sidebar or sidebar.group(1)!=expected_gpl_home:
        errors.append(f'GPLSpec sidebar logo should link to its own home {expected_gpl_home}')
    suite=re.search(r'<header class="sitebar">(.*?)</header>',gpl_home_html,re.S)
    if not suite:
        errors.append('GPLSpec page is missing the shared suite header')
    else:
        suite_links=dict((label.strip(),href) for href,label in re.findall(r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>',suite.group(1),re.S))
        if suite_links.get('Home')!=expected_suite_home or suite_links.get('GPLSpec')!=expected_gpl_home:
            errors.append('Shared suite header home links changed or point to the wrong site root')
    for f in files:
        if 'polyfill.io' in f.read_text(errors='replace'):
            errors.append(f'{f.relative_to(site)}: obsolete polyfill.io script remains')
    nav_script=site/'gplspec/assets/js/navigation.js'
    nav_css=site/'gplspec/assets/css/style.css'
    script_text=nav_script.read_text(errors='replace') if nav_script.is_file() else ''
    css_text=nav_css.read_text(errors='replace') if nav_css.is_file() else ''
    for marker in ('this.setAttribute("aria-expanded", String(expanded));','navLinks.classList.toggle("expanded", expanded);','document.querySelectorAll(".nav-links a.current")','parentSection.querySelector(".nav-toggle")','navLinksContainer.classList.add("expanded");','toggle.setAttribute("aria-expanded", "true");'):
        if marker not in script_text:errors.append(f'generated GPLSpec navigation script missing overlay behavior: {marker}')
    for marker in ('.nav-section .nav-toggle {','appearance: none;','font-family: inherit;'):
        if marker not in css_text:errors.append(f'generated GPLSpec stylesheet missing button overlay style: {marker}')
    fallback=re.search(r'<noscript>\s*<style>(.*?)</style>\s*</noscript>',gpl_home_html,re.S)
    if not fallback or not re.search(r'\.nav-toggle\s*\{[^}]*display:\s*none\s*!important',fallback.group(1)) or not re.search(r'\.nav-links\s*\{[^}]*max-height:\s*none\s*!important[^}]*overflow:\s*visible\s*!important',fallback.group(1)):
        errors.append('GPLSpec no-JavaScript fallback must expose navigation lists and hide inactive toggles')
    nav_page=Page(); nav_page.feed(gpl_home_html)
    toggles=[button for button in nav_page.buttons if 'nav-toggle' in button.get('class','').split()]
    if len(toggles)!=4:errors.append(f'GPLSpec navigation should expose four toggle buttons, found {len(toggles)}')
    control_ids=[button.get('aria-controls','') for button in toggles]
    if len(set(control_ids))!=len(control_ids):errors.append('GPLSpec navigation toggle aria-controls values are not unique')
    for button in toggles:
        target=button.get('aria-controls','')
        expanded=button.get('aria-expanded')
        if button.get('type')!='button' or not target or button.get('data-target')!=target or 'ul' not in nav_page.id_tags.get(target,set()) or nav_page.id_counts.get(target)!=1 or expanded not in ('true','false'):
            errors.append('GPLSpec navigation toggle lacks native button semantics or a valid matching controlled list')
        if expanded=='true' and 'expanded' not in button.get('class','').split():
            errors.append(f'GPLSpec navigation toggle {target} has mismatched expanded state')
        if expanded=='false' and 'expanded' in button.get('class','').split():
            errors.append(f'GPLSpec navigation toggle {target} has mismatched collapsed state')
    robots=site/'gplspec/robots.txt'
    if not robots.is_file() or robots.read_text().splitlines()!=['User-agent: *','Allow: /']:errors.append('GPLSpec robots.txt directives are not line-separated')
    # Doxygen generated output, including navigation and search resources, must be complete.
    api=site/'dspecm1d/api'
    if not (api/'index.html').is_file() or not (api/'search/search.js').is_file():errors.append('Doxygen index/search resources missing')
    if len(list(api.glob('*.html')))<180:errors.append('Doxygen API HTML page count unexpectedly low')
    # Approved asset hashes and counts are checked from the tracked bundle.
    manifest=json.loads((ROOT/'source-revisions.json').read_text())
    for name,spec in manifest['artwork'].items():
        if not isinstance(spec,dict) or not spec.get('sha256') or not spec.get('path'):continue
        f=ROOT/spec['path']; got=hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else None
        if got!=spec['sha256']:errors.append(f'asset identity mismatch: {name} expected {spec["sha256"]}, got {got}')
    excerpt=ROOT/'assets/data/dspecm1d-paper-ex1-excerpt.txt'
    if excerpt.is_file() and len(excerpt.read_text().splitlines())!=2881:errors.append('waveform excerpt row count is not 2881')
    home=(site/'index.html').read_text(errors='replace')
    for marker in ('viewport','skip-link','id="software"','id="about"','Follow the DSpecM1D example','Work in progress'):
        if marker not in home:errors.append(f'homepage missing expected content marker: {marker}')
    for marker in ('Working title','visual proposal','This proposal'):
        if marker.lower() in '\n'.join(f.read_text(errors='ignore') for f in files).lower():errors.append(f'prototype label remains: {marker}')
    # Source pages get a visible checkout instruction for their approved immutable pins.
    expected_pins={'gplspec':manifest['sources']['gplspec']['commit'],'DSpecM1D':manifest['sources']['DSpecM1D']['commit']}
    for rel,project in [('gplspec/getting-started/index.html','gplspec'),('dspecm1d/first-seismogram.html','DSpecM1D'),('dspecm1d/installation.html','DSpecM1D')]:
        page=site/rel
        content=page.read_text(errors='replace') if page.is_file() else ''
        pin=expected_pins[project]
        for fragment in (pin,f'git checkout {pin}',f'/commit/{pin}'):
            if fragment not in content:errors.append(f'{rel}: missing immutable checkout guidance {fragment}')
        blocks=re.findall(r'(?is)<pre\b.*?</pre>',content)
        plain='\n'.join(html.unescape(re.sub(r'<[^>]+>','',block)) for block in blocks)
        if project=='gplspec':
            setup_pattern=r'git clone https://github\.com/adcm2/gplspec\.git\s+cd gplspec\s+git checkout '+re.escape(pin)
        else:
            setup_pattern=r'cd DSpecM1D\s+git checkout '+re.escape(pin)
        if not re.search(setup_pattern,plain):errors.append(f'{rel}: setup code block does not clone/checkout the documented immutable pin')
    if 'This shared site brings three related but distinct projects into one place' not in home:errors.append('homepage shared-site wording changed or missing')
    print(f'HTML pages: {len(files)}; local links checked: {checked}; generated API HTML pages: {len(list(api.glob("*.html")))}')
    if errors:
        print(f'FAIL: {len(errors)} issue(s)'); print('\n'.join(errors[:80])); return 1
    print('PASS: local links, anchors, frozen route destinations, Doxygen search, and bundled asset identities')
    return 0
if __name__=='__main__':sys.exit(main())
