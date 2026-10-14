#!/usr/bin/env python3
"""Restore / repair hyperlinks in PDF2ZH translated PDFs.

Requires PyMuPDF (`pip install pymupdf`), no OCR or character-count mapping.

Usage:
    python pdf2zh-link-repair.py original.pdf translated.pdf
    python pdf2zh-link-repair.py original.pdf translated.dual.pdf --mode dual
    python pdf2zh-link-repair.py original.pdf translated.mono.pdf --mode mono

AUTO mode chooses two separate methods:

  DUAL (side-by-side, untranslated source still present): identify which
  half contains the source text, match unique 3-word TEXT FEATURES and their
  2-D coordinates on *each page*, fit/verify geometry and restore source
  link hitboxes to that original-language half. Reject ambiguous layouts.

  MONO (translated text, original page size): if the PDF already retains
  the original link annotations, validate *actual* annotation targets, page
  geometry and vector-drawing signatures, preserve the URI hotspots, and
  normalize named internal jumps. All targets are fixed independently of
  translation length, but translated text can reflow: emit a report of
  clickable rectangles that no longer intersect translated text. Do not
  invent locations for translated links that cannot be matched reliably.

Shared destination fixer normalizes valid source PDF named jumps and repairs
its malformed off-page targets by TOC/semantic features.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
import re
import statistics
import sys

import fitz


@dataclass(frozen=True)
class Transform:
    sx: float = 1.0
    sy: float = 1.0
    ox: float = 0.0
    oy: float = 0.0
    samples: int = 0
    inlier_ratio: float = 1.0

    def point(self, x: float, y: float) -> fitz.Point:
        return fitz.Point(self.ox + x * self.sx, self.oy + y * self.sy)

    def rect(self, r: fitz.Rect) -> fitz.Rect:
        a, b = self.point(r.x0, r.y0), self.point(r.x1, r.y1)
        return fitz.Rect(min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))


def box(side: str, dst: fitz.Page) -> fitz.Rect:
    x0 = dst.rect.width / 2 if side == 'right' else 0
    return fitz.Rect(x0, 0, x0 + dst.rect.width / 2, dst.rect.height)


def page_words(page: fitz.Page, clip: fitz.Rect | None = None):
    return page.get_text('words', clip=clip)


def word_features(words):
    # A unique 3-word sequence disambiguates repeated terms such as "the".
    # Every feature carries a physical PDF-coordinate, not a character offset.
    tokens = [w[4].casefold() for w in words]
    found = {}
    repeats = set()
    for i in range(1, len(words) - 1):
        key = (tokens[i - 1], tokens[i], tokens[i + 1])
        if len(''.join(key)) < 5:
            continue
        if key in found:
            repeats.add(key)
        else:
            w = words[i]
            found[key] = ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
    for k in repeats:
        del found[k]
    return found


def fit_line(pairs):
    # Robust iterative linear regression y = scale*x + shift.
    if len(pairs) < 2:
        raise ValueError('insufficient feature pairs')

    def least_squares(data):
        n = len(data)
        mx = sum(a for a, _ in data) / n
        my = sum(b for _, b in data) / n
        variance = sum((a - mx)**2 for a, _ in data)
        slope = sum((a - mx)*(b - my) for a, b in data)/variance if variance > 1 else 1.0
        return slope, my - slope*mx

    slope, off = least_squares(pairs)
    # Trim outliers; continue when a minority of text extraction chunks differ.
    for _ in range(3):
        deviations = [abs(b - (slope*a + off)) for a,b in pairs]
        median = statistics.median(deviations)
        threshold = max(1.5, median*3)
        good = [ab for ab, err in zip(pairs,deviations) if err <= threshold]
        if len(good) < max(2, 0.70*len(pairs)):
            break
        slope,off = least_squares(good)
    return slope,off


def model_for_page(src: fitz.Page, dual: fitz.Page, side: str) -> Transform:
    src_words = page_words(src)
    if not src_words:
        # Blank pages have no text features. Safe only when there are no links.
        if src.get_links():
            raise ValueError('source page has hyperlinks but no text features')
        return Transform(ox=box(side, dual).x0, samples=0)
    dst_words = page_words(dual, box(side,dual))
    a,b = word_features(src_words),word_features(dst_words)
    keys = a.keys() & b.keys()
    correspondences = [(a[k],b[k]) for k in keys]
    if len(correspondences) < 8:
        # For sparse pages, exact single-word spatial fingerprints still work.
        same = Counter((w[4], round(w[0],1),round(w[1],1)) for w in src_words)
        offset = box(side,dual).x0
        bilingual = Counter((w[4],round(w[0]-offset,1),round(w[1],1)) for w in dst_words)
        exact = sum((same & bilingual).values())/len(src_words)
        if exact >= .98:
            return Transform(ox=offset,samples=len(src_words))
        raise ValueError(f'Only {len(correspondences)} unique text features; exact signature={exact:.1%}')

    # Use three-word feature centroids to determine both axis scales and offsets.
    sx,ox = fit_line([(aa[0],bb[0]) for aa,bb in correspondences])
    sy,oy = fit_line([(aa[1],bb[1]) for aa,bb in correspondences])
    errors=[max(abs(sx*aa[0]+ox-bb[0]), abs(sy*aa[1]+oy-bb[1]))
            for aa,bb in correspondences]
    inliers=sum(err <= 1.5 for err in errors)
    ratio=inliers / len(errors)
    if ratio < .95 or not (0.3<=sx<=2.5 and 0.3<=sy<=2.5):
        raise ValueError(f'Geometric features do not match (samples={len(errors)}, inliers={ratio:.1%}, scales={sx:.3f},{sy:.3f})')
    # Correct geometry must remain entirely on the chosen half.
    r=Transform(sx,sy,ox,oy,len(errors),ratio).rect(src.rect)
    if not box(side,dual).contains(r) and not box(side,dual).contains(r + fitz.Rect(0.2,0.2,-0.2,-0.2)):
        # Tolerate sub-point numeric rounding and tiny crop differences.
        clip=box(side,dual)
        if r.x0 < clip.x0-1 or r.x1 > clip.x1+1 or r.y0 < -1 or r.y1 > dual.rect.height+1:
            raise ValueError(f'Inferred page geometry is outside {side} half: {r}')
    return Transform(sx,sy,ox,oy,len(errors),ratio)


def norm_title(text):
    text = text.replace('\xa0',' ')
    text = re.sub(r'^\s*(?:CHAPTER\s*)?\d+(?:\.\d+)*\.?\s*', '', text, flags=re.I)
    return ' '.join(re.sub(r'[^a-z0-9]+',' ',text.casefold()).split())


def toc_destination(original:fitz.Document, raw_page:int, name:str, anchor_text:str):
    """Recover buggy/off-page Red Hat PDF named links by semantic outline match."""
    toc=original.get_toc(simple=False)
    chapters=[i for i, (lvl,title,page,_) in enumerate(toc)
              if lvl==1 and title.replace('\xa0',' ').upper().startswith('CHAPTER') and page-1 <= raw_page]
    if not chapters:
        return None
    ch=chapters[-1]
    end=next((i for i in range(ch+1,len(toc)) if toc[i][0]==1),len(toc))
    candidates=toc[ch:end]
    # #installing-<chapter> anchors with no nested suffix denote whole chapters.
    if name.startswith('#installing-') and '_' not in name:
        heading=candidates[0]
        return heading[2]-1, original[heading[2]-1].rect.height - heading[3]['to'].y, heading[1]

    text=anchor_text.strip()
    variants = [norm_title(text), norm_title(' '.join(text.split()[:16])),
                norm_title(' '.join(text.split()[:10]))]
    if ':' in text:
        variants.append(norm_title(text.split(':')[0]))
    terms=set(norm_title(text).split())
    ranked=[]
    for level,title,page,detail in candidates:
        heading=norm_title(title)
        if not heading:
            continue
        similarity=max(SequenceMatcher(None,variant,heading).ratio() for variant in variants if variant)
        section_terms=set(heading.split())
        jaccard=len(terms & section_terms)/max(1,len(terms | section_terms))
        score=similarity + .20*jaccard
        ranked.append((score,page,detail,title))
    if not ranked:
        return None
    score,page,detail,title=max(ranked,key=lambda x:x[0])
    if score < .55:
        return None
    return page-1, original[page-1].rect.height-detail['to'].y, title


def repair_named_link(original, source_page:fitz.Page, link, counters:Counter, repair_log:list):
    # PyMuPDF LINK_NAMED 'to' is a raw *PDF* point (origin: lower-left).
    # For standard destinations, convert y to top-down page coordinates.
    target_page=link.get('page',-1)
    if target_page < 0 or target_page>=original.page_count:
        raise ValueError(f'Named destination page invalid: {link.get("nameddest")}')
    target=original[target_page]
    to=link.get('to')
    if to is None:
        raise ValueError('Named destination has no target point')
    name=link.get('nameddest','')
    if 0<=to.x<=target.rect.width and 0<=to.y<=target.rect.height:
        return target_page,fitz.Point(to.x,target.rect.height-to.y)

    # A few footnote / numbered callout names have invalid y in the source.
    # Do not invent their exact paragraph: retain their declared page.
    if re.fullmatch(r'#CO\d+-\d+',name):
        counters['approximate_callout_destination']+=1
        repair_log.append((source_page.number+1,name,target_page+1,'approximate: page top'))
        return target_page,fitz.Point(53.1, 20.0)

    # Original PDF itself has impossible destinations (for example y=19096).
    # Use the original PDF's section outline and anchor label to recover.
    visible=source_page.get_textbox(link['from']).replace('\n',' ').strip()
    found=toc_destination(original,target_page,name,visible)
    if found:
        p, y, title=found
        repair_log.append((source_page.number+1, name, p+1, title))
        counters['repaired_named_destination']+=1
        return p,fitz.Point(53.1,max(0.0,min(y,original[p].rect.height)))
    raise ValueError(f'Cannot resolve original off-page destination: {name}')


def restore(original_path:Path, dual_path:Path, outpath:Path, side:str):
    if outpath.resolve() in (original_path.resolve(),dual_path.resolve()):
        raise ValueError('Output must not overwrite either input')
    if outpath.exists():
        raise ValueError(f'Output already exists: {outpath}')
    stats=Counter()
    repair_log=[]
    with fitz.open(original_path) as src,fitz.open(dual_path) as dual:
        if len(src)!=len(dual):
            raise ValueError(f'Page counts differ: {len(src)} vs {len(dual)}')
        for i in range(len(src)):
            if src[i].rotation != dual[i].rotation or src[i].rotation:
                raise ValueError(f'Rotated page {i+1} requires a different transform')
            ratio=dual[i].rect.width/src[i].rect.width
            if not (1.85<ratio<2.15 and .94<dual[i].rect.height/src[i].rect.height<1.06):
                raise ValueError(f'Page {i+1} is not side-by-side bilingual layout')
        if side=='auto':
            # Compare *actual 3-word text features*, not a number of characters.
            test=next((i for i in range(len(src)) if len(page_words(src[i]))>60),None)
            if test is None:
                raise ValueError('No substantial text page available to identify source side')
            a=word_features(page_words(src[test]))
            scores={}
            for candidate in ('left','right'):
                b=word_features(page_words(dual[test],box(candidate,dual[test])))
                scores[candidate]=len(a.keys() & b.keys())/max(1,len(a))
            side=max(scores,key=scores.get)
            if scores[side]<.5:
                raise ValueError(f'Cannot find original-text side from word features: {scores}')
            print(f'Identified source-language side: {side} (3-word features: {scores})')

        print(f'Validating position features for {len(src)} pages ...',flush=True)
        transformations=[]
        for i in range(len(src)):
            try:
                tf=model_for_page(src[i],dual[i],side)
            except ValueError as ex:
                raise ValueError(f'Page {i+1}: {ex}') from ex
            transformations.append(tf)
        verified=sum(bool(tf.samples) for tf in transformations)
        print(f'Validated text/position features on {verified} content pages (other pages blank).',flush=True)

        for i,sp in enumerate(src):
            dp=dual[i]
            transform=transformations[i]
            for l in sp.get_links():
                rect=transform.rect(l['from'])
                rect &= box(side,dp)
                if rect.is_empty:
                    raise ValueError(f'Page {i+1}: link bbox not on {side} side: {l}')
                kind=l['kind']
                if kind==fitz.LINK_URI:
                    new={'kind':fitz.LINK_URI,'from':rect,'uri':l['uri']}
                    stats['external_uri']+=1
                elif kind in (fitz.LINK_NAMED,fitz.LINK_GOTO):
                    if kind==fitz.LINK_NAMED:
                        target_page,source_top=repair_named_link(src,sp,l,stats,repair_log)
                    else:
                        target_page=l['page']
                        source_top=l.get('to',fitz.Point(0,0))
                    dest=transformations[target_page].point(source_top.x,source_top.y)
                    new={'kind':fitz.LINK_GOTO,'from':rect,'page':target_page,'to':dest}
                    stats['internal_goto']+=1
                else:
                    raise ValueError(f'Page {i+1}: unsupported source link type {kind}')
                dp.insert_link(new)
        # Links and existing outline are added without editing page appearance.
        dual.save(outpath,garbage=3,deflate=True)
        stats['source_total']=sum(len(p.get_links()) for p in src)
        stats['source_outline']=len(src.get_toc())

    with fitz.open(outpath) as done:
        stats['output_total']=sum(len(p.get_links()) for p in done)
        stats['output_outline']=len(done.get_toc())
        if stats['output_total']!=stats['source_total']:
            raise ValueError('Post-save link count did not match source')
        if stats['source_outline']!=stats['output_outline']:
            raise ValueError('Post-save document bookmarks did not match source')

    print('\nRESULT')
    for key in ['source_total','external_uri','internal_goto','repaired_named_destination',
                'approximate_callout_destination','output_total','source_outline','output_outline']:
        print(f'  {key}: {stats[key]}')
    print(f'  output: {outpath}')
    if repair_log:
        audit=outpath.with_suffix('.link-repairs.txt')
        with open(audit,'w',encoding='utf-8') as f:
            f.write('Original PDF off-page named links resolved against document bookmarks.\n')
            for row in repair_log:
                f.write(f'from_pdf_page={row[0]} source_named_dest={row[1]} target_pdf_page={row[2]} destination={row[3]}\n')
        print(f'  audit: {audit}')



def _links_match(source_link, existing_link):
    """Annotation identity is a stronger mono feature than word count."""
    if source_link['kind'] != existing_link['kind']:
        return False
    if source_link['from'] != existing_link['from']:
        return False
    if source_link['kind'] == fitz.LINK_URI:
        return source_link.get('uri') == existing_link.get('uri')
    if source_link['kind'] == fitz.LINK_NAMED:
        return (source_link.get('nameddest') == existing_link.get('nameddest')
                and source_link.get('page') == existing_link.get('page'))
    if source_link['kind'] == fitz.LINK_GOTO:
        return source_link.get('page') == existing_link.get('page')
    return False


def _toc_cutoff(original):
    # Identify the first real document chapter, not by hardcoded page number.
    toc = original.get_toc(simple=False)
    chapters = [page - 1 for level, title, page, _ in toc
                if level == 1 and re.match(r'^CHAPTER\s+\d+', title.replace('\xa0', ' '), re.I)]
    return min(chapters) if chapters else 0


def restore_mono(original_path: Path, translated_path: Path, outpath: Path):
    """Preserve mono links already present, repair internal destinations.

    Translation breaks semantic English/Chinese text matching. We therefore
    intentionally do NOT infer hotspot positions from character counts or an
    unchecked page-wide transform: retained annotations provide the true
    source-link identity but not necessarily translated-label geometry.
    """
    if outpath.resolve() in (original_path.resolve(),translated_path.resolve()):
        raise ValueError('Output must not overwrite an input')
    if outpath.exists():
        raise ValueError(f'Output already exists: {outpath}')
    stats=Counter()
    audit=[]
    uncovered=[]
    with fitz.open(original_path) as original, fitz.open(translated_path) as mono:
        if len(original)!=len(mono):
            raise ValueError(f'Page counts differ: {len(original)} vs {len(mono)}')
        toc_end=_toc_cutoff(original)
        print(f'Validating {len(original)} mono pages by existing annotation identities and drawing features ...', flush=True)
        for pi,(src, dst) in enumerate(zip(original,mono)):
            if src.rect != dst.rect or src.rotation != dst.rotation:
                raise ValueError(f'Page {pi+1}: page dimensions or rotation differ; cannot assume preserved hotspots')
            # Layout fingerprint: stable non-text graphical features.  The
            # translation may alter text shape, but these must stay aligned.
            src_drawings, dst_drawings=src.get_drawings(),dst.get_drawings()
            if len(src_drawings)!=len(dst_drawings):
                raise ValueError(f'Page {pi+1}: non-text drawing fingerprint differs ({len(src_drawings)} vs {len(dst_drawings)})')
            stats['verified_pages']+=1
            src_links, dst_links=src.get_links(),dst.get_links()
            if len(src_links)!=len(dst_links):
                raise ValueError(f'Page {pi+1}: translated PDF has {len(dst_links)} links vs source {len(src_links)}. '
                                 'Mono content is translated, so copying link positions without matched features would be unreliable.')
            for j,(a,b) in enumerate(zip(src_links,dst_links)):
                if not _links_match(a,b):
                    raise ValueError(f'Page {pi+1}: link #{j+1} differs in target/hitbox; cannot safely pair')
            if not src_links:
                continue
            translated_word_boxes=[fitz.Rect(w[:4]) for w in dst.get_text('words')]
            for j,l in enumerate(src_links):
                if not any(l['from'].intersects(b) for b in translated_word_boxes):
                    flag='TOC' if pi<toc_end else 'BODY'
                    uncovered.append((pi+1,flag,j+1,str(l.get('uri',l.get('nameddest','')))))
                    stats['no_text_toc' if pi<toc_end else 'no_text_body']+=1
                stats['validated_links']+=1
        # URI annotations are already correct. Rewriting named annotations
        # IN PLACE is safer than deleting / inserting: some PDF generators
        # share or reorder annotation array objects, and delete_link() can
        # silently remove unrelated URI annotations on those pages.
        for pi,src in enumerate(original):
            dst=mono[pi]
            src_links=src.get_links()
            old_links=dst.get_links()
            for source_link,old_link in zip(src_links,old_links):
                kind=source_link['kind']
                if kind==fitz.LINK_URI:
                    stats['uri_preserved']+=1
                    continue
                if kind==fitz.LINK_NAMED:
                    target_page,source_top=repair_named_link(original,src,source_link,stats,audit)
                elif kind==fitz.LINK_GOTO:
                    target_page=source_link['page']
                    source_top=source_link.get('to',fitz.Point(0,0))
                else:
                    raise ValueError(f'Page {pi+1}: unsupported source link type: {kind}')
                if (not 0<=target_page<len(mono)
                    or not mono[target_page].rect.contains(source_top)):
                    raise ValueError(f'Page {pi+1}: invalid repaired destination: page={target_page}, to={source_top}')
                # PDF native destinations use bottom-up page Y, while
                # PyMuPDF's fitz.Point uses top-down page coordinates.
                target=mono[target_page]
                x=source_top.x
                pdf_y=target.rect.height-source_top.y
                target_page_obj=mono.page_xref(target_page)
                value=f'[{target_page_obj} 0 R /XYZ {x:.4f} {pdf_y:.4f} null]'
                mono.xref_set_key(old_link['xref'],'Dest',value)
                stats['internal_repaired']+=1
        mono.save(outpath,garbage=3,deflate=True)
        stats['source_outline']=len(original.get_toc())
    with fitz.open(outpath) as restored:
        stats['output_total']=sum(len(p.get_links()) for p in restored)
        stats['output_outline']=len(restored.get_toc())
        kind_counts=Counter(l['kind'] for p in restored for l in p.get_links())
        stats['output_urls']=kind_counts[fitz.LINK_URI]
        stats['output_gotos']=kind_counts[fitz.LINK_GOTO]
        if stats['output_total']!=stats['validated_links']:
            raise ValueError(f'Output link count mismatch: {stats["output_total"]} vs {stats["validated_links"]}')
        if stats['output_outline']!=stats['source_outline']:
            raise ValueError('Output outline count changed')
        if stats['output_urls']!=stats['uri_preserved'] or stats['output_gotos']!=stats['internal_repaired']:
            raise ValueError('Post-save URI / GOTO count mismatch')
    report=outpath.with_suffix('.link-report.txt')
    with report.open('w',encoding='utf-8') as f:
        f.write('MONO PDF LINK QUALITY REPORT\n')
        f.write('Restored destinations in an already-annotated translated PDF.\n')
        f.write('No translated content/coordinates were changed.\n\n')
        for key in ['verified_pages','validated_links','uri_preserved','internal_repaired',
                    'repaired_named_destination','approximate_callout_destination',
                    'output_total','output_urls','output_gotos','source_outline','output_outline',
                    'no_text_toc','no_text_body']:
            f.write(f'{key}: {stats[key]}\n')
        f.write('\nWARNING: MONO translations can reflow text. Source link rectangles\n'
                'are preserved rather than shifted by guessed character counts.\n'
                'The following original hitboxes do NOT intersect any translated\n'
                'text, even though their hyperlink actions exist and can be clicked.\n'
                'Some intersecting hotspots may nonetheless cover the wrong text.\n'
                'The TOC may have major translation-layout damage; this tool does not\n'
                'rebuild its visual appearance.\n\n')
        for pg,zone,n,target in uncovered:
            f.write(f'page={pg}, zone={zone}, link={n}, target={target}\n')
        f.write('\nOff-page named destination repairs:\n')
        for pg,name,targetpage,reason in audit:
            f.write(f'from_page={pg}, original_name={name}, repaired_page={targetpage}, reason={reason}\n')
    print('\nMONO RESULT',flush=True)
    for key in ['verified_pages','validated_links','uri_preserved','internal_repaired',
                'repaired_named_destination','approximate_callout_destination',
                'output_total','no_text_toc','no_text_body']:
        print(f'  {key}: {stats[key]}')
    print(f'  output: {outpath}\n  audit: {report}')


def classify_mode(original_path: Path, translated_path: Path):
    with fitz.open(original_path) as src, fitz.open(translated_path) as translated:
        if not len(src) or len(src)!=len(translated):
            raise ValueError('Source and translated PDF must have identical page counts')
        tests=[i for i in (0, len(src)//2, len(src)-1)]
        ratios=[translated[i].rect.width/src[i].rect.width for i in tests]
        heights=[translated[i].rect.height/src[i].rect.height for i in tests]
        if all(1.85 < r < 2.15 for r in ratios) and all(.94<h<1.06 for h in heights):
            return 'dual'
        if all(.99<r<1.01 for r in ratios) and all(.99<h<1.01 for h in heights):
            return 'mono'
        raise ValueError(f'Unrecognized translated layout widths={ratios}, heights={heights}')


def main():
    ap=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('original',type=Path,help='PDF with intact links')
    ap.add_argument('translated',type=Path,help='translated bilingual or monolingual PDF')
    ap.add_argument('--mode',choices=['auto','dual','mono'],default='auto')
    ap.add_argument('--side',choices=['auto','left','right'],default='auto',help='dual only: side of source text')
    ap.add_argument('-o','--output',type=Path)
    args=ap.parse_args()
    out=args.output or args.translated.with_name(args.translated.stem+'.with_links.pdf')
    try:
        mode=classify_mode(args.original,args.translated) if args.mode=='auto' else args.mode
        print(f'Selected mode: {mode}',flush=True)
        if mode=='dual':
            restore(args.original,args.translated,out,args.side)
        else:
            restore_mono(args.original,args.translated,out)
    except Exception as e:
        print(f'ERROR: {e}',file=sys.stderr)
        sys.exit(1)

if __name__=='__main__':
    main()