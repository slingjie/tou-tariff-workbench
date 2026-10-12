// 计算器回归：旧页面(线上) vs 新构建，逐区域/分类/电压比较月度结果表与 CSV（去掉时段条说明）
const {JSDOM}=require('jsdom');const fs=require('fs');
function load(p){const dom=new JSDOM(fs.readFileSync(p,'utf8'),{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x.test/'});return dom.window;}
const O=load(process.argv[2]||'old.html'),N=load(process.argv[3]||'../out/index.html');
const W=[['08:00','16:00'],['00:00','00:00']];
function setup(w){w.document.getElementById('tbCalc').click();}
setup(O);setup(N);
const g=(w,id)=>w.document.getElementById(id),ch=(w,id,v)=>{const e=g(w,id);e.value=v;e.dispatchEvent(new w.Event('change'));e.dispatchEvent(new w.Event('input'));};
let n=0,diff=[];
for(const r of [...g(O,'sR').options].map(o=>o.value)){ch(O,'sR',r);ch(N,'sR',r);
 for(const c of [...g(O,'sC').options].map(o=>o.value)){ch(O,'sC',c);ch(N,'sC',c);
  for(const v of [...g(O,'sV').options].map(o=>o.value)){ch(O,'sV',v);ch(N,'sV',v);
   for(const [s,e] of W){for(const w of [O,N]){ch(w,'start',s);ch(w,'end',e);}
    n++;const a=g(O,'mBody').textContent,b=g(N,'mBody').textContent,ca=g(O,'csv').value,cb=g(N,'csv').value;
    if(a!==b||ca!==cb) diff.push([r,c,v,s,e]);}}}}
console.log('cases',n,'diffs',diff.length,JSON.stringify(diff.slice(0,5)));process.exit(0);
