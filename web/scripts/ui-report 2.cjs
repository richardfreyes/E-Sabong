/* Build a local evidence gallery; no network or application writes. */
const fs = require('node:fs/promises');
const path = require('node:path');
const root = path.resolve(__dirname, '../../docs/ui-ux');
const escape = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
(async () => {
  const routes = JSON.parse(await fs.readFile(path.join(root, 'after/inventory.json'), 'utf8'));
  const flows = JSON.parse(await fs.readFile(path.join(root, 'after/flows.json'), 'utf8'));
  const extras = await fs.readFile(path.join(root, 'after/flows-extras.json'), 'utf8').then(JSON.parse).catch(() => []);
  flows.push(...extras);
  const files = (await fs.readdir(path.join(root, 'after'))).filter(name => name.endsWith('.png')).sort();
  const before = new Set(await fs.readdir(path.join(root, 'before')));
  const cards = files.map(file => {
    const label = file.replace(/\.png$/, '').replaceAll('-', ' ');
    const figure = (phase, title) => `<figure><figcaption>${title}</figcaption><a href="${phase}/${escape(file)}" target="_blank"><img loading="lazy" src="${phase}/${escape(file)}" alt="${escape(label)} — ${title}"></a></figure>`;
    return `<details data-label="${escape(label)}"><summary>${escape(label)}</summary><div class="pair">${before.has(file) ? figure('before', 'Baseline') : '<p>Additional verification state; no matching baseline capture.</p>'}${figure('after', 'After')}</div></details>`;
  }).join('\n');
  const html = `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Portal UI audit — screenshot evidence</title>
<style>html{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#090b10;color:#f5f7fa;font:15px/1.6 system-ui,sans-serif}main{max-width:1500px;margin:auto;padding:32px 20px}h1{font-size:clamp(24px,4vw,40px);letter-spacing:-.04em;line-height:1.2}p{max-width:85ch;color:#a9b6c9}a{color:#f5c542}label{display:block;margin:24px 0 6px}input{font:inherit;max-width:100%;width:520px;padding:12px;border:1px solid #64748b;border-radius:8px;background:#121620;color:inherit}details{margin-top:12px;border:1px solid #293244;border-radius:12px;background:#121620}summary{padding:16px;cursor:pointer;text-transform:capitalize}summary:focus-visible,a:focus-visible{outline:2px solid #f5c542;outline-offset:3px}.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;padding:16px}figure{margin:0;min-width:0}figcaption{margin-bottom:8px;font-weight:600}img{display:block;width:auto;max-width:100%;height:auto;border:1px solid #293244;border-radius:6px}details[hidden]{display:none}@media(max-width:700px){.pair{grid-template-columns:minmax(0,1fr)}main{padding:20px 12px}}</style>
<main><h1>Owner, Admin & Player screenshot evidence</h1><p>${routes.length} main routes at nine widths; ${flows.length} additional flows at three widths. ${files.length} final screenshots. These captures continue the existing working tree; they are not a comparison with the application before Claude's earlier work. Data and live counters may differ between captures.</p><p><a href="README.md">Read findings, fixes, test results and limitations</a> · <a href="after/inventory.json">Viewport measurements</a> · <a href="flow-coverage.md">Detailed flow coverage</a></p><label for="query">Find a portal, page, state or width</label><input id="query" type="search" placeholder="For example: player dashboard 320" autocomplete="off"><p>Open a comparison, then select an image to inspect the original screenshot.</p>${cards}</main><script>document.querySelector('#query').addEventListener('input',event=>{const words=event.target.value.toLowerCase().trim().split(/\\s+/);document.querySelectorAll('details').forEach(item=>{item.hidden=!words.every(word=>item.dataset.label.includes(word))})})</script></html>`;
  await fs.writeFile(path.join(root, 'gallery.html'), html);
  const steps = flows.map((flow, index) => `| ${index + 35} | ${flow.key} | ${flow.checks.some(c => c.pageOverflow || c.contentOverflow || c.dialogOverflow) ? 'Review measurements' : 'Pass; page and dialog fit'} | [320px](after/${flow.key}-320.png), [768px](after/${flow.key}-768.png), [1440px](after/${flow.key}-1440.png) |`).join('\n');
  await fs.writeFile(path.join(root, 'flow-coverage.md'), `# Detail, dialog, tab and navigation coverage\n\nContinues main-route steps 1–34 in [the report](README.md). Health refers to the captured state, not all possible data or permissions. Long text inputs can scroll internally; hidden file inputs and switch shadows are normal.\n\n| Step | Captured state | Health | Evidence |\n| --- | --- | --- | --- |\n${steps}\n`);
  console.log(`Wrote gallery with ${files.length} screenshots and ${flows.length} flow coverage steps.`);
})();
