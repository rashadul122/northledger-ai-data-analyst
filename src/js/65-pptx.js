/*@nl-file 65-pptx.js*/
/* window.NLPptx: a PowerPoint (.pptx) deck of the AI report, written in this browser (AI Analyst v2 phase G, 20 Oct 2026). PresentationML
   in a stored zip (NLX.zip, 56-xlsx.js), no library and nothing uploaded. One title slide, the executive summary, a slide per report
   section (its sentences as bullets, its confidence line kept), a slide per drawn figure (the analyst's visuals and the forecast, as
   PNG from the page's own SVG: NLDocx.svgToPng), the methods and the sources. Every word and figure is the report's as shown: the deck
   is the same text, cut into slides, never rewritten.

     NLPptx.deck({ title, subtitle, sections: [{ heading, lines: [text] }], figures: [{ title, caption, png, w, h }],
                   methods: [text], sources: [{ title, link }] }) -> Uint8Array (the .pptx file)
     NLPptx.fromReport(text) -> { title, sections }   the report text as the page shows it, cut into sections */
(function () {
  'use strict';
  var NLPptx = window.NLPptx = {};
  var EMU = 12700;                                   // EMUs a point
  var W = 960 * EMU, H = 540 * EMU;                  // 16:9 at 960 x 540 points
  var BULLETS_MAX = 7, BULLET_CHARS = 260;
  function x(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' }[c]; }).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, ''); }
  function cut(s, n) { s = String(s || '').replace(/\s+/g, ' ').trim(); if (s.length <= n) return s; var t = s.slice(0, n); var i = t.lastIndexOf(' '); return (i > n * 0.6 ? t.slice(0, i) : t) + '…'; }

  // the report text (markdown-lite, as the page renders it) into its title and sections
  NLPptx.fromReport = function (text) {
    var lines = String(text || '').split('\n'), title = '', sections = [], cur = null;
    lines.forEach(function (L) {
      var t = L.trim();
      if (!t || /^\[(?:CHART|TABLE):\d+\]$/.test(t) || /^\[VISUAL:[a-z0-9_]+\]$/.test(t) || /^\|/.test(t)) return;
      if (/^##\s/.test(t)) { cur = { heading: t.replace(/^##\s*/, ''), lines: [] }; sections.push(cur); return; }
      if (!cur) { if (!title) title = t; return; }
      cur.lines.push(t.replace(/^[-*]\s+/, '').replace(/^\d+\.\s+/, '').replace(/\s*\[S(\d+)\]/g, ' [$1]'));
    });
    return { title: title, sections: sections.filter(function (s) { return s.lines.length; }) };
  };

  function sp(id, name, xx, y, w, h, body, opts) {
    opts = opts || {};
    return '<p:sp><p:nvSpPr><p:cNvPr id="' + id + '" name="' + x(name) + '"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>' +
      '<p:spPr><a:xfrm><a:off x="' + xx + '" y="' + y + '"/><a:ext cx="' + w + '" cy="' + h + '"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>' +
      (opts.fill ? '<a:solidFill><a:srgbClr val="' + opts.fill + '"/></a:solidFill>' : '<a:noFill/>') + '</p:spPr>' +
      '<p:txBody><a:bodyPr wrap="square" lIns="91440" tIns="45720" rIns="91440" bIns="45720"><a:normAutofit/></a:bodyPr><a:lstStyle/>' + body + '</p:txBody></p:sp>';
  }
  function para(text, o) {
    o = o || {};
    var rpr = '<a:rPr lang="en-US" sz="' + (o.sz || 1800) + '"' + (o.b ? ' b="1"' : '') + ' dirty="0"><a:solidFill><a:srgbClr val="' + (o.color || '1A1A1A') + '"/></a:solidFill>' +
      '<a:latin typeface="Calibri"/></a:rPr>';
    var ppr = o.bullet ? '<a:pPr marL="285750" indent="-285750"><a:spcBef><a:spcPts val="600"/></a:spcBef><a:buFont typeface="Arial"/><a:buChar char="•"/></a:pPr>'
      : '<a:pPr><a:spcBef><a:spcPts val="' + (o.before || 0) + '"/></a:spcBef><a:buNone/></a:pPr>';
    return '<a:p>' + ppr + '<a:r>' + rpr + '<a:t>' + x(text) + '</a:t></a:r></a:p>';
  }
  function slideXml(shapes) {
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" ' +
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">' +
      '<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/>' +
      '<a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>' + shapes + '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>';
  }
  function pic(id, rid, xx, y, w, h, descr) {
    return '<p:pic><p:nvPicPr><p:cNvPr id="' + id + '" name="Figure ' + id + '" descr="' + x(descr) + '"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>' +
      '<p:blipFill><a:blip r:embed="' + rid + '"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="' + xx + '" y="' + y + '"/><a:ext cx="' + w + '" cy="' + h + '"/></a:xfrm>' +
      '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>';
  }
  var M = 40 * EMU;                                   // margin
  function titleBar(text) { return sp(2, 'Title', M, 24 * EMU, W - 2 * M, 64 * EMU, para(cut(text, 140), { sz: 2600, b: true })); }
  function bulletsSlide(heading, lines) {
    var body = lines.slice(0, BULLETS_MAX).map(function (t) {
      var conf = /^Confidence:/.test(t);
      return para(cut(t, BULLET_CHARS), { sz: conf ? 1300 : 1600, bullet: !conf, color: conf ? '555555' : '1A1A1A', before: conf ? 200 : 0 });
    }).join('') + (lines.length > BULLETS_MAX ? para('(' + (lines.length - BULLETS_MAX) + ' more in the full report)', { sz: 1200, color: '777777', before: 400 }) : '');
    return slideXml(titleBar(heading) + sp(3, 'Body', M, 96 * EMU, W - 2 * M, H - 130 * EMU, body));
  }

  NLPptx.deck = function (d) {
    d = d || {};
    var slides = [], media = [];
    // 1 title
    slides.push({ xml: slideXml(sp(2, 'Title', M, 150 * EMU, W - 2 * M, 120 * EMU, para(cut(d.title || 'Report', 160), { sz: 3400, b: true })) +
      sp(3, 'Subtitle', M, 280 * EMU, W - 2 * M, 120 * EMU, para(d.subtitle || '', { sz: 1600, color: '555555' }) +
        para('Figures computed by the NorthLedger engine in the browser; every figure was checked against the engine’s results or a cited source.', { sz: 1200, color: '777777', before: 600 }))) });
    // 2 sections, a slide each (a long one split in two)
    (d.sections || []).forEach(function (s) {
      for (var i = 0; i < s.lines.length; i += BULLETS_MAX) slides.push({ xml: bulletsSlide(s.heading + (i ? ' (continued)' : ''), s.lines.slice(i, i + BULLETS_MAX + (s.lines.length - i - BULLETS_MAX === 1 ? 1 : 0))) });
    });
    // 3 figures, one a slide, fitted inside the slide below its title
    (d.figures || []).forEach(function (f) {
      if (!f || !f.png || !f.w || !f.h) return;
      media.push(f.png);
      var n = media.length, boxW = W - 2 * M, boxH = H - 150 * EMU, r = Math.min(boxW / f.w, boxH / f.h), w = Math.round(f.w * r), h = Math.round(f.h * r);
      slides.push({ xml: slideXml(titleBar(f.title) + pic(4, 'rIdImg', Math.round((W - w) / 2), 96 * EMU, w, h, f.title + (f.caption ? ': ' + f.caption : '')) +
        (f.caption ? sp(5, 'Caption', M, H - 48 * EMU, W - 2 * M, 40 * EMU, para(cut(f.caption, 220), { sz: 1100, color: '555555' })) : '')), img: n });
    });
    if ((d.methods || []).length) slides.push({ xml: bulletsSlide('Methods: how every figure was computed', d.methods.map(function (t) { return cut(t, 200); })) });
    if ((d.sources || []).length) slides.push({ xml: bulletsSlide('Sources', d.sources.map(function (s, i) { return '[' + (i + 1) + '] ' + (s.title || '') + (s.link ? ' — ' + s.link : ''); })) });

    var files = [], ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>',
      '<Default Extension="png" ContentType="image/png"/>',
      '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
      '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
      '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
      '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
      '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
      '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'];
    slides.forEach(function (s, i) { ct.push('<Override PartName="/ppt/slides/slide' + (i + 1) + '.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'); });
    ct.push('</Types>');
    files.push({ name: '[Content_Types].xml', data: ct.join('') });
    files.push({ name: '_rels/.rels', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>' +
      '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>' +
      '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>' });
    var now = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
    files.push({ name: 'docProps/core.xml', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" ' +
      'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>' + x(cut(d.title || 'Report', 200)) +
      '</dc:title><dc:creator>NorthLedger</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">' + now + '</dcterms:created></cp:coreProperties>' });
    files.push({ name: 'docProps/app.xml', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>NorthLedger</Application><Slides>' + slides.length + '</Slides></Properties>' });
    files.push({ name: 'ppt/presentation.xml', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" ' +
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">' +
      '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>' +
      slides.map(function (s, i) { return '<p:sldId id="' + (256 + i) + '" r:id="rId' + (i + 2) + '"/>'; }).join('') +
      '</p:sldIdLst><p:sldSz cx="' + W + '" cy="' + H + '"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>' });
    files.push({ name: 'ppt/_rels/presentation.xml.rels', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>' +
      slides.map(function (s, i) { return '<Relationship Id="rId' + (i + 2) + '" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide' + (i + 1) + '.xml"/>'; }).join('') +
      '<Relationship Id="rId' + (slides.length + 2) + '" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/></Relationships>' });
    var emptyTree = '<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld>';
    var NS = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"';
    files.push({ name: 'ppt/slideMasters/slideMaster1.xml', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sldMaster ' + NS + '>' + emptyTree +
      '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>' +
      '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>' });
    files.push({ name: 'ppt/slideMasters/_rels/slideMaster1.xml.rels', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>' +
      '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>' });
    files.push({ name: 'ppt/slideLayouts/slideLayout1.xml', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sldLayout ' + NS + ' type="blank" preserve="1">' + emptyTree +
      '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>' });
    files.push({ name: 'ppt/slideLayouts/_rels/slideLayout1.xml.rels', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>' });
    var clr = function (n, v) { return '<a:' + n + '><a:srgbClr val="' + v + '"/></a:' + n + '>'; };
    files.push({ name: 'ppt/theme/theme1.xml', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="NorthLedger">' +
      '<a:themeElements><a:clrScheme name="NorthLedger"><a:dk1><a:srgbClr val="1A1A1A"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>' + clr('dk2', '1F2937') + clr('lt2', 'F3F4F6') +
      clr('accent1', '2563EB') + clr('accent2', 'D97706') + clr('accent3', '059669') + clr('accent4', '9333EA') + clr('accent5', 'DC2626') + clr('accent6', '0891B2') +
      clr('hlink', '2563EB') + clr('folHlink', '7C3AED') + '</a:clrScheme><a:fontScheme name="NorthLedger"><a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>' +
      '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme><a:fmtScheme name="NorthLedger"><a:fillStyleLst>' +
      '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>' +
      '<a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>' +
      '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle>' +
      '<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' +
      '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>' });
    slides.forEach(function (s, i) {
      files.push({ name: 'ppt/slides/slide' + (i + 1) + '.xml', data: s.xml });
      files.push({ name: 'ppt/slides/_rels/slide' + (i + 1) + '.xml.rels', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>' +
        (s.img ? '<Relationship Id="rIdImg" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image' + s.img + '.png"/>' : '') + '</Relationships>' });
    });
    media.forEach(function (png, i) { files.push({ name: 'ppt/media/image' + (i + 1) + '.png', data: png }); });
    return window.NLX.zip(files);
  };
})();
