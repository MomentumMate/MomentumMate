/* MomentumMate – paritetstest (2026-11-04, modell 2.0 2026-11-05)
   Kör appens förslagsmotor (autoPlan) och en oberoende referens skriven direkt från SPEC.md på samma data
   och samma portföljer, och jämför rad för rad. Avviker de är något oprövat inblandat i appen.
   Körning:  node parity_test.js [katalog med index.html + data.json]   (standard: .)
   Kräver: node + playwright (Chromium). Avslutar med kod 1 vid avvikelse. */
const {chromium}=require('playwright');const http=require('http'),fs=require('fs'),path=require('path');
const DIR=path.resolve(process.argv[2]||'.');
const PORT=8779;
const srv=http.createServer((q,r)=>{const u=q.url.split('?')[0];const f=path.join(DIR,u==='/'?'index.html':u);fs.readFile(f,(e,b)=>{if(e){r.writeHead(404);r.end();return}r.writeHead(200,{'Content-Type':f.endsWith('.json')?'application/json':'text/html'});r.end(b)})}).listen(PORT);

/* ---------- Referensen: SPEC.md § 3–9, skriven utan att titta på appens kod ---------- */
function reference(ctx,p,o){
  const {S,fx,minTrade,monthOpen,quarterOpen}=ctx;
  const MIN_POST=32222, COST_N=7, FULL_N=20;   // SPEC 2.0 § 4: 7 innehav i kostnadsläge (backtest #78)
  // SPEC 2.0 § 3: poäng = sc2 (Clenow 120 d), lika poäng → högst cl. Saknas sc2 i datat gäller 1.0 (sc, m121).
  const SC=x=>typeof x.sc2==="number"?x.sc2:x.sc, TIE=x=>typeof x.cl==="number"?x.cl:(+x.m121||0);
  const isNord=t=>/\.(ST|CO|OL|HE)$/.test(t);
  const num=v=>typeof v==="number"&&isFinite(v);
  const isNew=s=>{const nb=+s.nb||0;if(!nb)return false;return (!!s.sh&&nb<201)||(!num(s.m121)&&nb<300)};
  const px=h=>{const s=S[h.t];return s&&s.px>0?s.px:(h.lp>0?h.lp:h.c)};
  const F=h=>{const s=S[h.t];return fx[s?s.ccy:(h.ccy||"SEK")]||1};
  const val=h=>h.q*px(h)*F(h);
  // § 3 rankning: poäng, lika poäng → högst 12-1
  const ranked=Object.values(S).filter(x=>x&&x.px>0&&SC(x)>0&&x.inf!==false).sort((a,b)=>(SC(b)-SC(a))||(TIE(b)-TIE(a))||(a.t<b.t?-1:a.t>b.t?1:0));   // SPEC § 18: exakt lika Clenow → alfabetisk ticker
  const rank={};ranked.forEach((x,i)=>rank[x.t]=i+1);
  const rk=t=>rank[t]||1e9;
  const tot=p.h.reduce((a,h)=>a+val(h),0);
  const amt=+o.cash||0,free=!(amt>0),tot1=tot+(free?0:amt);
  // § 4 portföljstorlek (inkl. nytt kapital)
  const cost=tot1<MIN_POST*5,want=p.targetN||(cost?COST_N:FULL_N);
  const FLOOR=1500;
  const rows=[];const sold=new Set();let proceeds=0;
  const add=(t,a,q)=>rows.push({t,a,q});
  // § 6 hållregeln vid månadsprövning
  p.h.forEach(h=>{const s=S[h.t];if(!s||!(px(h)>0)||!(h.q>0)||isNew(s))return;
    const badM=num(s.m121)&&s.m121<=0,badS=!s.sh&&!s.a200;
    if((badM||badS)&&monthOpen){sold.add(h.t);proceeds+=val(h);add(h.t,"SÄLJ",h.q)}});
  let live=p.h.filter(h=>!sold.has(h.t)&&S[h.t]);
  // § 8 kostnadsläge: utländska säljs
  if(cost){live.filter(h=>!isNord(h.t)).forEach(h=>{sold.add(h.t);proceeds+=val(h);add(h.t,"SÄLJ",h.q)});live=live.filter(h=>!sold.has(h.t))}
  // § 4 fler än målet
  if(live.length>want){live.slice().sort((a,b)=>rk(a.t)-rk(b.t)).slice(want).forEach(h=>{sold.add(h.t);proceeds+=val(h);add(h.t,"SÄLJ",h.q)});live=live.filter(h=>!sold.has(h.t))}
  // § 7 likaviktning
  const tgt=tot1/want,rwBuy=[];
  if(quarterOpen)live.forEach(h=>{const v=val(h),per=px(h)*F(h);
    if(v>tgt*1.25){const q=Math.floor((v-tgt)/per);if(q>=1&&q<h.q&&q*per>=FLOOR){proceeds+=q*per;add(h.t,"SÄLJ",q)}}
    else if(v<tgt*0.75&&!isNew(S[h.t]))rwBuy.push({t:h.t,cur:v})});
  // § 5 köp
  const budget=free?proceeds:amt+(o.incl?proceeds:0),held=new Set(p.h.map(h=>h.t));
  const top=cost?ranked.filter(x=>isNord(x.t)).slice(0,want):ranked.slice(0,want);
  const slots=Math.max(0,want-live.length);
  const fresh=top.filter(x=>!held.has(x.t)&&!sold.has(x.t)&&!isNew(x)).slice(0,slots).map(x=>({t:x.t,cur:0}));
  let cands=rwBuy.sort((a,b)=>rk(a.t)-rk(b.t)).concat(fresh);
  if(!free&&!slots&&!rwBuy.length)cands=live.filter(h=>!isNew(S[h.t])&&val(h)<tgt).sort((a,b)=>val(a)-val(b)).map(h=>({t:h.t,cur:val(h)}));
  let pool=budget;
  for(const x of cands){if(pool<FLOOR)break;const s=S[x.t],per=s.px*(fx[s.ccy]||1),room=Math.max(0,tgt-x.cur),q=Math.floor(Math.min(room,pool)/per);if(q<1||q*per<FLOOR)continue;pool-=q*per;add(x.t,"KÖP",q)}
  // § 6/9 behåll
  p.h.forEach(h=>{if(!sold.has(h.t))add(h.t,"BEHÅLL",0)});
  // slå ihop samma aktie + åtgärd
  const m={};const out=[];rows.forEach(r=>{const k=r.t+"|"+r.a;if(m[k]){if(r.a!=="BEHÅLL")m[k].q+=r.q}else{m[k]=Object.assign({},r);out.push(m[k])}});
  return out;
}

(async()=>{
  const b=await chromium.launch();const pg=await b.newPage();const errs=[];pg.on('pageerror',e=>errs.push(e.message));
  await pg.goto(`http://localhost:${PORT}/`);await pg.waitForTimeout(2500);
  const info=await pg.evaluate(()=>({updated:D.updated,n:Object.keys(S).length,appv:APPV}));
  console.log(`Data ${info.updated} · ${info.n} aktier · app ${info.appv}`);
  const load=f=>{try{return JSON.parse(fs.readFileSync(path.join(DIR,f),'utf8')).h}catch(e){return null}};
  const top=await pg.evaluate(()=>Object.values(S).filter(x=>x&&x.px>0&&SC(x)>0&&x.inf!==false).sort(bySc).map(x=>x.t));
  const pick=await pg.evaluate(()=>{const o={};const all=Object.values(S).filter(x=>x&&x.px>0&&typeof x.m121==="number");
    o.bad=all.filter(x=>x.m121<=0||!x.a200).slice(0,6).map(x=>x.t);o.nord=all.filter(x=>/\.ST$/.test(x.t)&&x.a200&&x.m121>0).slice(0,8).map(x=>x.t);
    o.neu=Object.values(S).filter(x=>x&&x.px>0&&x.nb&&x.nb<150).slice(0,2).map(x=>x.t);return o});
  const mk=(h,extra)=>Object.assign({id:"t",name:"T",h:h.map(x=>Object.assign({d:"2026-09-01"},x)),tx:[],cash:0,rules:null,targetN:null,qrw:null,mchk:null},extra||{});
  const hq=(t,sek)=>({t,q:1,c:1});   // antal sätts nedan efter kurs
  const byVal=async(ts,sek)=>pg.evaluate(([ts,sek])=>ts.map(t=>{const s=S[t];return{t,q:Math.max(1,Math.floor(sek/(s.px*fx(s.ccy)))),c:s.px*0.8,n:s.n,ccy:s.ccy}}),[ts,sek]);
  const scen=[];
  // Verkliga portföljer
  for(const f of ["nordea_uppdatering.json","portfolio_import.json","mate600i_innehav.json","mate600m_innehav.json"]){const h=load(f);if(!h)continue;
    scen.push({name:f+" (månad+kvartal)",p:mk(h,{mchk:"2026-10-01"}),o:{cash:0}});
    scen.push({name:f+" (bara kvartal)",p:mk(h,{mchk:"2026-11-04"}),o:{cash:0}});
    scen.push({name:f+" (inget fönster, nytt kapital 200 000)",p:mk(h,{mchk:"2026-11-04",qrw:"2026-10-20"}),o:{cash:200000,incl:false}})}
  // Syntetiska
  const healthy=await byVal(top.slice(0,20),100000);scen.push({name:"20 friska topp-20, inget fönster",p:mk(healthy,{mchk:"2026-11-04",qrw:"2026-10-20"}),o:{cash:0}});
  scen.push({name:"20 friska, kvartal, en på 3× vikt",p:mk(healthy.map((x,i)=>i===0?Object.assign({},x,{q:x.q*3}):x),{mchk:"2026-11-04"}),o:{cash:0}});
  const bad=await byVal(pick.bad.slice(0,4).concat(top.slice(0,12)),100000);scen.push({name:"4 brutna + 12 friska, månadsfönster öppet",p:mk(bad,{mchk:"2026-10-01",qrw:"2026-10-20"}),o:{cash:0}});
  scen.push({name:"samma, fönster stängt",p:mk(bad,{mchk:"2026-11-04",qrw:"2026-10-20"}),o:{cash:0}});
  const small=await byVal(["AAPL","MSFT"].concat(pick.nord.slice(0,5)),14000);scen.push({name:"kostnadsläge 100 000 kr med 2 utländska",p:mk(small,{mchk:"2026-10-01"}),o:{cash:0}});
  const many=await byVal(top.slice(0,24),80000);scen.push({name:"24 innehav (fler än målet)",p:mk(many,{mchk:"2026-11-04",qrw:"2026-10-20"}),o:{cash:0}});
  if(pick.neu.length){const nh=await byVal(top.slice(0,19).concat([pick.neu[0]]),100000);nh[19].q=Math.max(1,Math.floor(nh[19].q/5));scen.push({name:"nynoterad under målvikt vid kvartal",p:mk(nh,{mchk:"2026-11-04"}),o:{cash:50000,incl:false}})}
  scen.push({name:"tom portfölj, 1 000 000 nytt",p:mk([],{}),o:{cash:1000000}});
  scen.push({name:"tom portfölj, 100 000 nytt (kostnadsläge)",p:mk([],{}),o:{cash:100000}});
  let fails=0;
  for(const sc of scen){
    const ticks=[...new Set(sc.p.h.map(h=>h.t).concat(top.slice(0,40)))];
    const ctx=await pg.evaluate(([p,ticks])=>{const mt={};ticks.forEach(t=>mt[t]=minTrade(t));const fxm={};Object.values(S).forEach(s=>{if(s&&s.ccy&&!(s.ccy in fxm))fxm[s.ccy]=fx(s.ccy)});fxm.SEK=1;
      return{fx:fxm,minTrade:mt,monthOpen:monthCheckWindow(p).open,quarterOpen:!!quarterDue(p)}},[sc.p,ticks]);
    const Sdump=await pg.evaluate(()=>{const o={};Object.values(S).forEach(s=>{if(s)o[s.t]={t:s.t,px:s.px,sc:s.sc,sc2:s.sc2,cl:s.cl,m121:s.m121,a200:s.a200,sh:s.sh,nb:s.nb,ccy:s.ccy,inf:s.inf}});return o});
    const app=await pg.evaluate(([p,o])=>autoPlan(p,o).items.map(i=>({t:i.t,a:i.a,q:i.q})),[sc.p,sc.o]);
    const ref=reference(Object.assign({S:Sdump},ctx),sc.p,sc.o);
    const key=r=>r.t+"|"+r.a+"|"+(r.a==="BEHÅLL"?0:r.q);
    const A=new Set(app.map(key)),R=new Set(ref.map(key));
    const onlyA=[...A].filter(k=>!R.has(k)),onlyR=[...R].filter(k=>!A.has(k));
    const ok=!onlyA.length&&!onlyR.length;if(!ok)fails++;
    const cnt=a=>app.filter(i=>i.a===a).length;
    console.log(`${ok?"OK  ":"FEL "} ${sc.name}  [månad ${ctx.monthOpen?"öppet":"stängt"}, kvartal ${ctx.quarterOpen?"öppet":"stängt"}]  SÄLJ ${cnt("SÄLJ")} · KÖP ${cnt("KÖP")} · BEHÅLL ${cnt("BEHÅLL")}`);
    if(!ok){console.log("   bara i appen:     "+onlyA.join(", "));console.log("   bara i referensen: "+onlyR.join(", "))}
  }
  if(errs.length)console.log("Sidfel: "+errs.join(" | "));
  await b.close();srv.close();
  console.log(fails?`\n${fails} scenario(n) avviker.`:"\nAlla scenarion lika – appen följer specifikationen.");
  process.exit(fails||errs.length?1:0);
})();
