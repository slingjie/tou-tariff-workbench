// 工作台全组合扫描：报错 / NaN / 特殊日默认
const {JSDOM}=require('jsdom');const fs=require('fs');
const dom=new JSDOM(fs.readFileSync(process.argv[2]||'../out/index.html','utf8'),{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x.test/'});
const w=dom.window,d=w.document,g=id=>d.getElementById(id),ch=(id,v)=>{g(id).value=v;g(id).dispatchEvent(new w.Event('change'));};
let errs=[],n=0,bad=[],sp={};w.addEventListener('error',e=>errs.push(e.message));
for(const r of [...g('sR').options].map(o=>o.value)){ch('sR',r);
 for(const m of [...g('sM').options].map(o=>o.value)){ch('sM',m);
  for(const c of [...g('sC').options].map(o=>o.value)){ch('sC',c);
   for(const v of [...g('sV').options].map(o=>o.value)){try{ch('sV',v);n++;}catch(e){errs.push(r+m+c+v+e.message)}
    if(/NaN|undefined/.test(g('kpis').textContent+g('c24').textContent+g('notice').textContent)) bad.push([r,m,c,v]);
    if(g('sD').value!=='normal'){(sp[r+' '+g('sD').value]=sp[r+' '+g('sD').value]||new Set()).add(m)}}}}}
console.log('combos',n,'errors',errs.length,errs.slice(0,5),'NaN',bad.length,JSON.stringify(bad.slice(0,5)));
for(const k in sp) console.log('default special',k,[...sp[k]].sort().join(','));process.exit(0);
