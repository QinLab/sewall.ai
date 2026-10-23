/* Exercise the actual self-contained viewer script with a minimal DOM.
 * This checks interaction behavior; it is not a browser layout/screenshot test.
 * Run: node tests/knowledge_map_viewer.cjs knowledge-map/index.html
 */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor(tag) {this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.style={};this.listeners={};this.textContent='';this.value='';}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children=nodes;}
  setAttribute(k,v) {this.attributes[k]=v;}
  addEventListener(k,v) {this.listeners[k]=v;}
  scrollIntoView() {}
  click() {this.listeners.click?.({preventDefault(){}});}
  querySelectorAll(selector) {let out=[];for(const c of this.children){if(selector==='.node' && String(c.attributes.class).split(' ').includes('node'))out.push(c);out.push(...c.querySelectorAll(selector));}return out;}
}
const ids=new Map(['summary','graph','detail','view','search','status','results','clear','download','legend','footer'].map(id=>[id,new Element('div')]));
ids.get('view').value='sources';
const document={getElementById:id=>ids.get(id),createElement:t=>new Element(t),createElementNS:(_,t)=>new Element(t)};
const html=fs.readFileSync(process.argv[2],'utf8');
const script=html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
const ctx=vm.createContext({document,URL,Blob,setTimeout});
new vm.Script(script).runInContext(ctx);
let checks=0;
function count(n,edges){assert.equal(ids.get('graph').querySelectorAll('.node').length,n);assert.match(ids.get('status').textContent,new RegExp(`${n} nodes and ${edges} edges`));checks++;}
function text(n){return n.textContent+' '+n.children.map(text).join(' ');}
count(39,29);
for(const [mode,n,e] of [['types',69,54],['policies',56,36],['all',116,111]]){ids.get('view').value=mode;ids.get('view').listeners.change();count(n,e);}
ids.get('clear').click();count(39,29);
ids.get('search').value='NEON';ids.get('search').listeners.input();assert.equal(ids.get('results').style.display,'block');checks++;
const result=ids.get('results').children.find(n=>n.tagName==='button' && n.textContent==='NEON Data Portal and API');assert.ok(result);result.click();assert.match(text(ids.get('detail')),/Catalog entry/);assert.match(text(ids.get('detail')),/governance|Governance/);checks++;
ids.get('clear').click();ids.get('view').value='policies';ids.get('view').listeners.change();const policy=ids.get('graph').querySelectorAll('.node').find(n=>n.dataset.id==='review:service:neon');assert.ok(policy);policy.listeners.keydown({key:'Enter',preventDefault(){}});assert.match(text(ids.get('detail')),/Needs record-level review/);assert.match(text(ids.get('detail')),/egress: unknown/);checks++;
ids.get('clear').click();assert.match(text(ids.get('detail')),/Explore the catalog/);assert.equal(ids.get('search').value,'');checks++;
console.log(JSON.stringify({passed:true,interaction_checks:checks,browser_visual_qa:false,scope:'actual embedded script with minimal DOM; no browser service available'},null,2));
