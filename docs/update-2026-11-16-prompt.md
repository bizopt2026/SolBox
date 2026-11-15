# 작업 지시: 11/16 업데이트 (주간 결과 · 체중 연동 · 바디·오늘 화면 개선 · 대회 주 카드)

> 아래 전체를 복사해서, **복제한 'Sub-3 훈련 체크' 앱을 만든 Claude 대화**에 붙여 넣으세요.
> 바로 아래 `<여기에 내 앱 링크>` 자리에 내 앱(복제본) 링크를 넣어 주세요.
> 11/15 업데이트 프롬프트(`update-2026-11-15-prompt.md`)를 먼저 적용한 앱을 기준으로 만들었어요.

---

내 아티팩트 "Sub-3 훈련 체크"에 아래 업데이트를 적용해서 **같은 URL에 다시 게시**해 줘.

- 아티팩트: <여기에 내 앱 링크>

## 0. 먼저 지킬 것
1. `artifact-capabilities` 스킬과 `artifact-design` 스킬을 먼저 로드해.
2. 아티팩트를 `read`로 읽고, **그 원본 파일을 고쳐서** 같은 `url`로 재게시해. 새 아티팩트를 만들지 마.
3. 게시할 때 `capabilities`와 `contract`를 넘기지 마. 기존 권한 선언을 그대로 이어가.
4. 아래에 적은 곳만 고치고, 다른 기능(COROS 보내기·가민 파일·식단·AI 코치·스토리 등)은 건드리지 마.
5. 앱 버전 값(`APP_VERSION`, `APP_RELEASED`, `VLOG`)이 있으면 **바꾸지 마.** 복제한 앱의 버전은 복제 시점 값으로 고정해서 관리해.
6. 아래 '찾기' 코드가 그대로 없으면, 같은 역할을 하는 곳을 찾아 같은 결과가 나오게 고치고 무엇을 바꿨는지 알려줘.

## 1. 바뀌는 내용
- **훈련표 · 주간 결과**
  - 오늘 탭 맨 위 주간 리포트를 없애요.
  - 끝난 주를 펼치면 결과 카드가 먼저 보여요: 완료 링·거리·평균 점수·체중·식단·요일별 점수 막대(포인트 날 ★)·최고 훈련·AI 주간 리뷰.
  - 요일별 기록은 '📅 일별 기록'으로 접혀요. 아직 안 끝난 주는 결과를 보여주지 않아요.
  - '지난 주' 묶음은 카드형으로 접고 펴고, 펼치면 지난 주들이 그 카드 안에 묶여 보여요. 접힘 상태는 기억해요.
- **체중 연동**
  - freddy(가민·애플) 체중을 연동 앱에 기록된 **측정 시각 그대로** 지난 1년치 모두 불러와요(중복 제외).
  - COROS는 측정 시각을 주지 않아서 '시각 미제공'으로 표시해요.
- **바디 탭**
  - 맨 위 지표는 색 화살표 표기로 통일해요: 체중 '목표까지 ▼4.7', 안정시 심박 '평소 ▼6', 수면 HRV '정상 ▲1.6'.
  - 체중·안정시 심박·식단 카드 머리는 큰 숫자 아래 한 줄로 변화를 보여주고, 오른쪽에는 상태만 둬요.
- **오늘 탭**
  - 오늘 훈련 카드를 날짜 줄 바로 아래로 올려요. 대회 주 카드는 주간 띠 아래로, 새 소식은 맨 아래로 옮겨요.
  - 워치를 연결하지 않았으면 연결 안내를 보여줘요.
  - 컨디션 심박은 색 화살표로 표시해요.
  - 완료한 날에는 💬 AI 분석 버튼을 넣어요.
- **공통**
  - 탭별 역할 안내(처음 한 번), 불러오는 동안 상단 진행 막대.
  - 툴바 보조 버튼은 연보라 채움, 열린 상태는 보라 채움으로 통일해요.
  - 카드 둥글기·간격 값을 통일하고, 11px보다 작은 글자를 없애요.
- **대회 주 카드**
  - 접힌 머리: 'D-6' 한 줄 배지(위에 '대회까지').
  - 펼친 화면: 준비 진행 막대, 항목별 시점 태그, '지금' 할 일 강조와 맨 위 정렬, 완료 항목은 아래로.
  - 카보로딩 안내를 6일 기준(D-6~D-4 체중×5~7g, D-3~D-1 체중×8~10g)으로 맞춰요.

## 2. 앱에 이미 있어야 하는 것 (확인만)
`S`, `save()`, `keepIf()`, `render()`, `view`, `esc()`, `toast()`, `T`, `W`, `TOTAL`, `planDay()`, `dateOf()`, `idxOf()`, `addDays()`, `md()`, `wn()`, `DOW`, `oneLine()`, `weekStats()`, `weekKm()`, `weekAvg()`, `wDay()`, `dietWeeks()`, `dayScore()`, `dayRec()`, `isDone()`, `band()`, `SCOL`, `enterSub()`, `coachAsk()`, `BACK`, `foldOpen()`, `foldSet()`, `addWeight()`, `localDate()`, `jsonBlobs()`, `allText()`, `fdiag()`, `lastOf()`, `bodyW()`, `AUTH`, `runsOn()`, `isGarmin()`가 없으면 `function isGarmin(){return S.watch==="freddy"}`를 추가해.

## 3. 새로 넣을 코드 (메인 스크립트, `function weekReport(){` 바로 앞)
### 3-1. 주간 결과
```js
var WRES={};
function weekResultHtml(w){var st=weekStats(w),pkm=weekKm(w),av=weekAvg(w),wk=[],ends=dateOf(w,6);
  for(var d=0;d<7;d++){var x=wDay(dateOf(w,d));if(x)wk.push(x.avg)}var dv=wk.length>1?Math.round((wk[wk.length-1]-wk[0])*10)/10:null;
  var dw=dietWeeks().filter(function(r){return r[0]===w})[0],cp=st.n?Math.round(st.done/st.n*100):0,kp=pkm?Math.min(100,Math.round(st.km/pkm*100)):0;
  var best=null,days='';for(var d2=0;d2<7;d2++){var ds=dateOf(w,d2),t=planDay(w,d2),sc=dayScore(ds),dn=isDone(ds),r=dayRec(ds);
    if(sc&&sc.s!=null&&!sc.extra&&(!best||sc.s>best.s))best={s:sc.s,ds:ds,t:t,d:d2};
    var has=sc&&sc.s!=null,cls=t[0]==="r"?(r.w?'ex':'rest'):dn?'ok':'miss',col=has?SCOL[band(sc.s)]:'',ht=has?Math.max(14,sc.s):cls==='miss'?100:0;
    var pt=t[0]==="q"||t[0]==="l"||t[0]==="x";days+='<div class="wrd '+cls+(pt?' wpt':'')+'" title="'+esc(md(ds)+' '+oneLine(t))+'"><span class="wrv num"'+(col?' style="color:'+col+'"':'')+'>'+(has?sc.s:cls==='rest'?'':cls==='miss'?'✕':'✓')+'</span><span class="wrcol"><u style="height:'+ht+'%'+(col?';background:'+col:'')+'"></u></span><small>'+(pt?'<i class="kstar" aria-label="포인트 훈련">★</i>':'')+DOW[d2]+'</small></div>'}
  var vd=!st.n?['–','na']:cp>=100&&(av==null||av>=85)?['훌륭한 한 주','ok']:cp>=80?['잘 지킨 한 주','ok']:cp>=50?['절반 이상 소화','mid']:['다시 리듬 잡기','bad'];
  var h='<div class="wres"><div class="wrhero"><div class="wrring" style="--p:'+cp+'"><i><b class="num">'+st.done+'</b><small>/'+st.n+'</small></i></div>'
    +'<div class="wrhm"><small>'+wn(w)+' 결과 · '+md(dateOf(w,0))+'–'+md(ends)+'</small><b>'+vd[0]+'</b><div class="wrkm"><span><b class="num">'+st.km+'</b> / '+pkm+'km</span><span class="num">'+kp+'%</span></div><div class="wrbar"><u style="width:'+kp+'%"></u></div></div></div>';
  h+='<div class="wrst">'+[["🎯","평균 점수",av!=null?'<b class="num" style="color:'+SCOL[band(av)]+'">'+av+'<i>점</i></b>':'<b>–</b>'],["⚖️","체중",dv==null?'<b>–</b>':'<b class="num" style="color:'+(dv<0?'var(--ok)':dv>0?'var(--bad)':'var(--ink)')+'">'+(dv>0?'+':dv<0?'−':'±')+Math.abs(dv)+'<i>kg</i></b>'],["🥗","식단",dw?'<b class="num" style="color:'+SCOL[band(dw[1])]+'">'+dw[1]+'<i>점</i></b>':'<b>–</b>']].map(function(x){return '<div><span aria-hidden="true">'+x[0]+'</span><small>'+x[1]+'</small>'+x[2]+'</div>'}).join('')+'</div>';
  h+='<div class="wrdays" aria-label="요일별 훈련 점수">'+days+'</div>';
  if(best)h+='<div class="wrbest"><span class="wrmed" aria-hidden="true">🏅</span><span class="wrbt"><small>이번 주 최고 훈련 · '+md(best.ds)+' '+DOW[best.d]+'</small><b>'+esc(oneLine(best.t))+'</b></span><b class="num wrbs" style="color:'+SCOL[band(best.s)]+'">'+best.s+'</b></div>';
  h+='<button type="button" class="wrai" data-wrai="'+w+'"><span aria-hidden="true">💬</span><span><b>AI 코치 주간 리뷰</b><small>잘한 점 · 아쉬운 점 · 다음 주 포인트</small></span><span class="wrarr" aria-hidden="true">→</span></button></div>';
  return h}
function weekDone(w){return dateOf(w,6)<T}
function weekResultBtn(w){if(!weekDone(w))return '';var on=!!WDAYS[w];
  return weekResultHtml(w)+'<button type="button" class="wdayb'+(on?' on':'')+'" data-wdays="'+w+'" aria-expanded="'+on+'"><span aria-hidden="true">📅</span><b>일별 기록</b><span class="wrsum">월~일 7일 · 체크·기록 보기</span><span class="chev" aria-hidden="true"></span></button>'}
var WDAYS={};
function bindWeekRes(){view.querySelectorAll("[data-wdays]").forEach(function(b){b.onclick=function(){var w=+b.dataset.wdays;WDAYS[w]=!WDAYS[w];var y=window.scrollY;render();window.scrollTo(0,y)}});
  view.querySelectorAll("[data-wrai]").forEach(function(b){b.onclick=function(){var w=+b.dataset.wrai,st=weekStats(w),av=weekAvg(w);enterSub("coach");if(BACK)BACK.ctx=true;render();
    coachAsk(wn(w)+"("+md(dateOf(w,0))+"~"+md(dateOf(w,6))+") 한 주 훈련을 리뷰해 줘. 완료 "+st.done+"/"+st.n+"회, "+st.km+"km(계획 "+weekKm(w)+"km)"+(av!=null?", 평균 점수 "+av+"점":"")+". 잘한 점, 아쉬운 점, 다음 주에 신경 쓸 포인트를 알려 줘.")}})}
```
### 3-2. freddy 체중 불러오기 (`async function freddySync(` 바로 앞에 넣어도 돼)
```js
function freddyWeightNames(lm){var out=[],seen={};String(lm||"").split("\n").forEach(function(l){var re=/`?([A-Za-z][A-Za-z0-9]*(?:[_.][A-Za-z0-9]+)*)`?/g,m;while((m=re.exec(l))){var n=m[1];if(seen[n])continue;
  if(/weight|body_?mass|bodymass/i.test(n)&&!/lean|fat|goal|bmi|muscle|bone|water|percent|pct|index|height/i.test(n)){seen[n]=1;out.push(n)}}});return out.slice(0,8)}
function fwKg(v,u,n){u=String(u||"").toLowerCase();if(/lb|pound/.test(u+" "+n))v=v*0.45359237;else if(u==="g"||/gram/.test(u)||v>1000)v=v/1000;return v>=30&&v<=200?Math.round(v*10)/10:null}
function fwTime(o,date){var keys=["timestamp","time","measuredAt","measured_at","recordedAt","startTime","start_time","startDate","sampleTime","date","calendarDate","localDate"];for(var i=0;i<keys.length;i++){var v=o[keys[i]];if(v==null)continue;
    if(typeof v==="number"){var ms=v>1e12?v:v>1e9?v*1000:null;if(ms)return ms}
    if(typeof v==="string"){if(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(v)){var t=Date.parse(v);if(!isNaN(t))return t}if(/^\d{4}-\d{2}-\d{2}$/.test(v)&&!date)date=v}}
  var sub=o.raw||o.data||o.record;if(sub&&typeof sub==="object"&&!Array.isArray(sub)){var t2=fwTime(sub,date);if(typeof t2==="number")return t2}
  return date?{day:date}:null}
function parseFreddyWeights(text){var out=[];
  jsonBlobs(text).forEach(function(b){(function walk(o,date){if(!o||typeof o!=="object")return;if(Array.isArray(o)){o.forEach(function(x){walk(x,date)});return}
    var d2=typeof o.date==="string"&&/^\d{4}-\d{2}-\d{2}/.test(o.date)?o.date.slice(0,10):date;
    Object.keys(o).forEach(function(k){var v=o[k];if(typeof v==="number"&&/weight|bodymass|body_mass|^value$/i.test(k)&&!/lean|fat|goal|bmi|muscle|bone|water|percent/i.test(k)){var kg=fwKg(v,o.unit||o.units||"",k),tm=fwTime(o,d2);if(kg&&tm)out.push({kg:kg,t:typeof tm==="number"?tm:null,day:tm.day||null})}
      else if(v&&typeof v==="object")walk(v,d2)})})(b,null)});
  if(!out.length)String(text||"").split("\n").forEach(function(l){var dm=l.match(/(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}):(\d{2}))?/);if(!dm||!/weight|body_?mass/i.test(l))return;var vm=l.match(/(?:weight|mass)[\w.]*\s*[:=]\s*(\d[\d.,]*)\s*([a-zA-Z]*)/i);if(!vm)return;
    var kg=fwKg(parseFloat(vm[1].replace(/,/g,"")),vm[2],"");if(!kg)return;var a=dm[1].split("-");out.push({kg:kg,t:dm[2]?new Date(+a[0],a[1]-1,+a[2],+dm[2],+dm[3]).getTime():null,day:dm[1]})});
  return out}
function addWeightsImported(list,src){var added=0;list.forEach(function(w){var ms=w.t,tu=false;if(!ms&&w.day){var a=w.day.split("-");ms=new Date(+a[0],a[1]-1,+a[2],7,0).getTime();tu=true}if(!ms)return;
    var ds=localDate(Math.floor(ms/1000)),arr=S.wlog[ds]||[];if(arr.some(function(x){return Math.abs(x.t-ms)<10*60000&&Math.abs(x.kg-w.kg)<0.15||(x.src===src&&Math.abs(x.kg-w.kg)<0.05&&(tu||x.tu))}))return;
    addWeight(ms,w.kg,src);if(tu){var e=S.wlog[ds].filter(function(x){return x.t===ms&&x.src===src})[0];if(e)e.tu=1}added++});return added}
async function freddyWeightSync(mcp,lm,CC,provs){var names=freddyWeightNames(lm);fdiag("체중 항목 "+names.length+"개: "+names.join(", "));if(!names.length)return 0;
  try{var q=await mcp.callTool("freddy","query_metrics",{metrics:names,start:addDays(T,-364),end:T,include_raw:true},{cache:CC}),qt=allText(q),ws=parseFreddyWeights(qt);
    fdiag("체중 query_metrics 응답 "+qt.length+"자 · 읽은 체중 "+ws.length+"개 · 앞부분: "+qt.replace(/\s+/g," ").slice(0,300));
    var src=provs&&provs.length===1?(provs[0]==="GARMIN"?"Garmin":provs[0]==="APPLE_HEALTH"?"애플 건강":provs[0]):"freddy";return addWeightsImported(ws,src)}catch(e){fdiag("체중 조회 실패: "+(e&&(e.code||e.message)));return 0}}
```
### 3-3. 탭 역할 안내 · 공통 진행 막대 (`function render(){` 바로 앞)
```js
var TABROLE={today:["오늘","오늘 할 훈련을 보고, 뛴 뒤 점수·기록·식단을 확인해요"],plan:["훈련표","계획을 세우고 일정을 조정해요. 지난 주 결과도 여기서 봐요"],pace:["페이스","내 기준 페이스를 정하고, 중간 점검과 레이스 전략을 준비해요"],log:["바디","체중·안정시 심박·수면 HRV·식단으로 몸 상태를 봐요"]};
function tabHint(){var r=TABROLE[tab];S.th=S.th||{};if(!r||S.th[tab]||AUTH.st==="anon")return;var el=document.createElement("div");el.className="tabhint";el.setAttribute("role","note");
  el.innerHTML='<span><b>'+r[0]+' 탭</b>'+r[1]+'</span><button type="button" aria-label="안내 닫기">확인</button>';el.querySelector("button").onclick=function(){S.th[tab]=1;save();el.remove()};view.insertBefore(el,view.firstChild)}
setInterval(function(){try{var b=!!((typeof WS!=="undefined"&&WS.busy)||(typeof WX!=="undefined"&&WX.busy)||(typeof CW!=="undefined"&&CW.busy)||(typeof CWD!=="undefined"&&CWD.busy)||(typeof AI!=="undefined"&&AI.busy)||(typeof STORY!=="undefined"&&STORY.busy)||(typeof RS!=="undefined"&&RS.busy)||(typeof GM!=="undefined"&&GM.busy));document.body.classList.toggle("busy",b)}catch(e){}},300);
```

## 4. 통째로 교체할 함수
### 4-1. `function bodyKpis(ld){…}` → 아래로 교체
```js
function bodyKpis(ld){
  var g=S.wgoal&&S.wgoal.target,r=lastOf(S.rhr),hv=lastOf(S.hrv),dw=dietWeeks(),dl=dw.length?dw[dw.length-1]:null;
  function wkDelta(get,keys,goodDown){var cur=[],prev=[];keys.forEach(function(k){var v=get(k);if(v==null)return;if(k>addDays(T,-7))cur.push(v);else if(k>addDays(T,-14))prev.push(v)});if(!cur.length||!prev.length)return '';
    var a=cur.reduce(function(x,y){return x+y},0)/cur.length,b=prev.reduce(function(x,y){return x+y},0)/prev.length,dv=Math.round((a-b)*10)/10;if(!dv)return '<small class="dlt">±0</small>';var good=goodDown?dv<0:dv>0;
    return '<small class="dlt" style="color:'+(good?'var(--ok)':'var(--bad)')+'" title="지난주 평균 대비">'+(dv>0?'▲':'▼')+Math.abs(dv)+'</small>'}
  var DL={wt:wkDelta(function(k){var x=wDay(k);return x?x.m:null},Object.keys(S.wlog||{}),true),rhr:wkDelta(function(k){return S.rhr[k]},Object.keys(S.rhr||{}),true),hrv:wkDelta(function(k){var x=S.hrv[k];return x&&x.avg?x.avg:null},Object.keys(S.hrv||{}),false)};
  var IC={wt:"⚖️",rhr:"❤️",hrv:"🌙",diet:"🥗"};
  function t(id,lb,v,u,sub,col){return '<button class="bk'+(BODYV===id?' on':'')+'" role="tab" data-bk="'+id+'" aria-selected="'+(BODYV===id)+'" aria-pressed="'+(BODYV===id)+'"><span class="bkl"><i aria-hidden="true">'+IC[id]+'</i>'+lb+'</span><b class="num"'+(col?' style="color:'+col+'"':'')+'>'+v+(v!=="—"?'<small>'+u+'</small>':'')+'</b><span class="bks">'+sub+(DL[id]&&id==="hrv"?' '+DL[id]:'')+'</span></button>'}
  var wc=null,rc=null,hc=null,rs='최근 아침';
  if(ld){var wp=wtPlan(),st=null;for(var i=1;i<wp.length;i++)if(wp[i][0]>=T){st=wp[i];break}
    var ov=st?ld.avg-st[2]:(g?ld.avg-g:null);if(ov!=null)wc=SCOL[ov<=0?5:ov<=0.5?4:ov<=1?3:ov<=2?2:1]}
  if(r){var rk=Object.keys(S.rhr||{}).sort().slice(-15,-1),ra=0;rk.forEach(function(d){ra+=S.rhr[d]});
    if(rk.length>=3){ra=ra/rk.length;var df=Math.round(r-ra);rc=SCOL[df<=0?5:df<=1?4:df<=2?3:df<=4?2:1];rs='평소 '+(df?'<small class="dlt" style="color:'+(df<0?'var(--ok)':'var(--bad)')+'">'+(df>0?'▲':'▼')+Math.abs(df)+'</small>':'<small class="dlt">±0</small>')}}
  if(hv&&hv.avg){if(hv.lo)hc=SCOL[hv.avg>=hv.lo?5:hv.avg>=hv.lo*0.95?3:1];else if(hv.ev)hc=SCOL[/Low|Poor/.test(hv.ev)?2:/Unbalanced/.test(hv.ev)?3:5]}
  var bi=["wt","rhr","hrv","diet"].indexOf(BODYV);return '<div class="bkgrid bseg" role="tablist" aria-label="바디 지표" style="--bi:'+Math.max(0,bi)+'"><span class="bind" aria-hidden="true"></span>'+t("wt","체중",ld?ld.avg:"—","kg",ld&&g?(ld.avg-g>0?'목표까지 <small class="dlt" style="color:var(--accent)">▼'+(Math.round((ld.avg-g)*10)/10)+'</small>':'목표 달성 🎉'):(g?'목표 '+g+'kg':'목표 미입력'),wc)+t("rhr","안정시 심박",r||"—","bpm",r?rs:'눌러서 시작',rc)+t("hrv","수면 HRV",hv&&hv.avg?hv.avg:"—","ms",hv&&hv.ev?({Normal:"정상",Low:"낮음",High:"높음",Balanced:"정상",Unbalanced:"불균형",Poor:"낮음"}[hv.ev]||esc(hv.ev)):'눌러서 시작',hc)+t("diet","식단 점수",dl?dl[1]:"—","",(function(){var c=carbLoad(T);return c&&!c.off?'🍝 카보로딩 D-'+c.left:dl?wn(dl[0])+' 평균':'눌러서 시작'})(),dl?SCOL[band(dl[1])]:null)+'</div>'}
```
### 4-2. `var RCHK=[…];`와 `function raceWeekCard(){…}` → 아래로 교체
```js
var RCHK=[["carb","카보로딩","D-6~D-4 탄수 체중×5~7g, D-3~D-1 체중×8~10g · 섬유·지방은 줄이기"],["plan","레이스 전략 확인","구간 페이스·보급 시점 다시 보기"],["kit","준비물","배번·칩, 젤 4~6개, 바셀린, 레이스 복장·신발"],["sleep","수면","대회 전 이틀은 7시간 이상"],["route","출발 동선","집결 시간·교통·물품 보관 확인"]];
function raceWeekCard(){if(sel!==T)return '';var nx=null;for(var k=0;k<=7;k++){var ds=addDays(T,k),ix=idxOf(ds);if(ix>=0&&ix<TOTAL&&planAt(ix)[0]==="x"){nx=ds;break}}
  (S.races||[]).forEach(function(rc){if(!nx&&rc.date>=T&&rc.date<=addDays(T,7))nx=rc.date});if(!nx)return '';
  var dd=Math.round((p(nx)-p(T))/864e5),rc=(S.races||[]).filter(function(x){return x.date===nx})[0],nm=rc?rc.name:(nx===RACE?'서울마라톤':(function(){var ix=idxOf(nx),tx=ix>=0&&ix<TOTAL?String(planAt(ix)[1]).split("\n")[0].trim():"";return tx&&tx.length<=20?tx:'대회'})()),ck=(S.raceChk||{})[nx]||{},n=RCHK.filter(function(x){return ck[x[0]]}).length;
  var op=foldOpen("rw",false),nx2=RCHK.filter(function(x){return !ck[x[0]]})[0],pct=Math.round(n/RCHK.length*100),IC={carb:"🍝",plan:"🗺️",kit:"🎒",sleep:"😴",route:"🚇"},WHEN={carb:"D-2~D-1",plan:"D-3까지",kit:"D-1",sleep:"D-2부터",route:"D-1"};
  var gl=rc&&rc.goal?fmtT(rc.goal):(nx===RACE&&S.goal?fmtT(S.goal.sec):""),dw=DOW[(new Date(p(nx)).getUTCDay()+6)%7];
  var hd='<button type="button" class="rwh" data-rwt aria-expanded="'+op+'" aria-label="'+esc(nm)+' 준비 '+(op?'접기':'펼치기')+'"><span class="rwd"><small>'+(dd?'대회까지':'오늘 대회')+'</small><b class="num">'+(dd?'D-'+dd:'🏁')+'</b></span><span class="rwt"><small>'+md(nx)+' ('+dw+')'+(gl?' · 목표 '+gl:'')+'</small><b>'+esc(nm)+'</b><em>'+(n===RCHK.length?'✓ 준비 완료':'다음 · '+esc(nx2[1]))+'</em></span><span class="rwring" style="--p:'+pct+'"><i class="num">'+n+'<small>/'+RCHK.length+'</small></i></span><span class="rwchev" aria-hidden="true"></span></button>';
  if(!op)return '<div class="card raceweek rwc rwfold">'+hd+'</div>';
  var WIN={carb:[6,1],plan:[7,3],kit:[2,1],sleep:[2,0],route:[2,1]},WL={carb:"D-6~D-1",plan:"D-7~D-3",kit:"D-2~D-1",sleep:"D-2~당일",route:"D-2~D-1"};
  var items=RCHK.map(function(x){var w=WIN[x[0]],on=!!ck[x[0]],now=!on&&dd<=w[0]&&dd>=w[1],late=!on&&dd<w[1],soon=!on&&dd>w[0];return{x:x,on:on,st:on?'done':now?'now':late?'late':'soon',w:w}});
  items.sort(function(a,b){var o={now:0,late:1,soon:2,done:3};return o[a.st]-o[b.st]||a.w[1]-b.w[1]});
  var nNow=items.filter(function(q){return q.st==="now"}).length;
  var body='<div class="rwbody"><div class="rwpg"><div><b>준비 체크리스트</b><small>'+(n===RCHK.length?'모두 끝났어요. 이제 푹 쉬고 출발만!':nNow?'지금 할 일 '+nNow+'개 · 눌러서 체크':'다가오는 할 일을 미리 확인해요')+'</small></div><span class="num">'+n+'<small>/'+RCHK.length+'</small></span></div><div class="rwpb"><u style="width:'+pct+'%"></u></div>';
  body+='<ul class="rwl">'+items.map(function(q){var x=q.x;return '<li><label class="rwi '+q.st+'"><input type="checkbox" class="rwin" data-rck="'+nx+'|'+x[0]+'"'+(q.on?' checked':'')+'><span class="rwic" aria-hidden="true">'+IC[x[0]]+'</span><span class="rwtx"><span class="rwtt"><b>'+x[1]+'</b>'+(q.st==="now"?'<em class="rwnow">지금</em>':'')+'<em class="rwwhen">'+WL[x[0]]+'</em></span><small>'+x[2]+'</small></span><span class="rwck" aria-hidden="true"></span></label></li>'}).join('')+'</ul>';
  body+='<button class="rwgo" type="button" data-goto="pace"><span class="rwgi" aria-hidden="true">🏁</span><span class="rwgt"><b>레이스 전략 보기</b><small>구간 페이스 · 보급 · 통과 시간</small></span><span class="rwarr" aria-hidden="true">→</span></button></div>';
  return '<div class="card raceweek rwc">'+hd+body+'</div>'}
```

## 5. 찾아서 바꿀 곳
**E1. 오늘 탭: 맨 위 주간 리포트를 없애고, 오늘 훈련 카드가 날짜 줄 바로 아래 오도록 순서를 바꿔요**

찾기:
```js
h+=authBar()+seasonCard()+newsCard()+startCard()+weekReport()+statusStrip();
```
바꾸기:
```js
h+=authBar()+seasonCard()+startCard()+statusStrip();
```

**E2. 오늘 탭: 날짜 줄 위의 대회 주 카드 줄을 지워요(아래로 옮겨요)**

찾기:
```js
  h+=raceWeekCard();
  h+='<div class="daynav">
```
바꾸기:
```js
  h+='<div class="daynav">
```

**E3. 오늘 탭: 식단 열 직전에 워치 연결 안내와 대회 주 카드를 넣고, 새 소식은 맨 아래로**

찾기:
```js
  h+='</div><div class="tcol">'+mealCard(sel,d,t);
  h+='</div></div>';
```
바꾸기:
```js
  if(!S.watch&&sel<=T&&t[0]!=="r")h+='<div class="nudge"><span aria-hidden="true">⌚</span><span><b>워치를 연결하면 뛴 기록이 자동으로 들어와요</b><small>COROS · 가민·애플(freddy) · 점수와 그래프가 함께 채워져요</small></span><button type="button" class="btn" data-start="watch">연결</button></div>';
  h+=raceWeekCard();
  h+='</div><div class="tcol">'+mealCard(sel,d,t);
  h+='</div></div>'+newsCard();
```

**E4. 오늘 탭: 완료한 날 결과 버튼 줄에 💬 AI 분석 버튼 추가**

찾기:
```js
+(canStory?'<button type="button" class="sbtn igbtn" data-story="'+sel+'">'+IGSVG+'인스타 스토리</button>':'')+'</div>'+(det?
```
바꾸기:
```js
+(canStory?'<button type="button" class="sbtn igbtn" data-story="'+sel+'">'+IGSVG+'인스타 스토리</button>':'')+((r.w||runsOn(sel).length)?'<button type="button" class="sbtn aibtn" data-coachrun="'+sel+'">💬 AI 분석</button>':'')+'</div>'+(det?
```

**E5. 훈련표: 지난 주 접힘 상태를 기억해요**

찾기:
```js
var PASTW=false;
```
바꾸기:
```js
var PASTW=null;
```

**E6. 훈련표: '지난 주 N개 보기' 줄을 카드형 머리로**

찾기:
```js
if(w===0&&cwk>0)h+='<details class="pastwk" id="pastwk"'+(PASTW?' open':'')+'><summary class="ifold-h"><span>지난 주 '+Math.min(cwk,W.length)+'개 보기</span><span class="chev" aria-hidden="true"></span></summary>';
```
바꾸기:
```js
if(PASTW===null)PASTW=foldOpen("pastw",false);if(w===0&&cwk>0){var pn=Math.min(cwk,W.length),pd=0,pt=0,pk=0;for(var pw2=0;pw2<pn;pw2++){var ps=weekStats(pw2);pd+=ps.done;pt+=ps.n;pk+=ps.km}
      h+='<details class="pastwk" id="pastwk"'+(PASTW?' open':'')+'><summary class="pwh"><span class="pwi" aria-hidden="true">🗂</span><span class="pwt"><b>지난 주 '+pn+'개</b><small>'+wn(0)+(pn>1?'–'+wn(pn-1):'')+' · 완료 '+pd+'/'+pt+' · '+Math.round(pk*10)/10+'km</small></span><span class="pwo">'+(PASTW?'접기':'펼치기')+'</span><span class="chev" aria-hidden="true"></span></summary>'}
```

**E7. 훈련표: 끝난 주는 결과를 먼저 보여주고 일별 기록은 접어요**

찾기:
```js
    if(openWeeks[w]){for(var d=0;d<7;d++){
```
바꾸기:
```js
    if(openWeeks[w])h+=weekResultBtn(w);
    if(openWeeks[w]&&(!weekDone(w)||WDAYS[w])){for(var d=0;d<7;d++){
```

**E8. 훈련표: 지난 주 접기 토글 기억**

찾기:
```js
var pw=document.getElementById("pastwk");if(pw)pw.addEventListener("toggle",function(){PASTW=pw.open});
```
바꾸기:
```js
var pw=document.getElementById("pastwk");if(pw)pw.addEventListener("toggle",function(){PASTW=pw.open;foldSet("pastw",pw.open);var po=pw.querySelector(".pwo");if(po)po.textContent=pw.open?"접기":"펼치기"});
```

**E9. 훈련표: 결과 버튼 연결**

찾기:
```js
bindMyPlan();bindPlanSrc();bindPlanStrip();
```
바꾸기:
```js
bindMyPlan();bindPlanSrc();bindPlanStrip();bindWeekRes();
```

**E10. 체중: freddy(가민·애플) 체중을 측정 시각 그대로 불러와요**

찾기:
```js
    var ms=freddyMetrics(lm);fdiag(
```
바꾸기:
```js
    var wAdded=await freddyWeightSync(mcp,lm,CC,provs);if(wAdded)toast("체중 "+wAdded+"개를 측정 시각 그대로 불러왔어요");
    var ms=freddyMetrics(lm);fdiag(
```

**E11. 체중: COROS 체중은 측정 시각을 주지 않아 '시각 미제공'으로 표시해요 (2곳 모두)**

찾기:
```js
var nt=Date.now();addWeight(nt,kg,"COROS");
```
바꾸기:
```js
var nt=Date.now();addWeight(nt,kg,"COROS");(function(){var e=(S.wlog[localDate(Math.floor(nt/1000))]||[]).filter(function(x){return x.t===nt})[0];if(e)e.tu=1})();
```

**E12. 체중 목록: 시각 미제공 표시**

찾기:
```js
aria-label="잰 시각 바꾸기">'+hhmm(x.t)+' ✎</button>
```
바꾸기:
```js
aria-label="잰 시각 바꾸기">'+(x.tu?'시각 미제공':hhmm(x.t))+' ✎</button>
```

**E13. 체중: 시각을 직접 고치면 '시각 미제공' 표시를 지워요**

찾기:
```js
if(!S.wlog[ds].length)delete S.wlog[ds];if(!x)return;
```
바꾸기:
```js
if(!S.wlog[ds].length)delete S.wlog[ds];if(!x)return;delete x.tu;
```

**E14. 체중 카드 머리: 1주 변화를 큰 숫자 아래 한 줄로**

찾기:
```js
<b class="num">'+ld.avg+'<i>kg</i></b></div>'+(wdl!=null?'<span class="wtchip '+(wdl<0?'ok':wdl>0?'bad':'')+'">'+(wdl<0?'▼':wdl>0?'▲':'±')+Math.abs(wdl)+'kg <small>1주 전보다</small></span>':'')
```
바꾸기:
```js
<b class="num">'+ld.avg+'<i>kg</i></b>'+(wdl!=null?'<span class="wtdl '+(wdl<0?'ok':wdl>0?'bad':'')+'">'+(wdl<0?'▼':wdl>0?'▲':'±')+Math.abs(wdl)+'kg<small> · 1주 전보다</small></span>':'')+'</div>'
```

**E15. 안정시 심박 카드 머리: 평소 대비를 큰 숫자 아래 한 줄로**

찾기:
```js
wthero rhhero"><div><small>최근 '+md(lk)+'</small><b class="num">'+lv+'<i>bpm</i></b></div>'+(ldf!=null?'<span class="wtchip '+(lst[0]==="bad"?'bad':lst[0]==="ok"?'ok':'')+'">'+(ldf>0?'▲':ldf<0?'▼':'±')+Math.abs(ldf)+' <small>2주 평균 '+avg+'</small></span><span class="rhst '+lst[0]+'">'+lst[1]+'</span>':'<span class="wtchip"><small>2주 평균은 기록이 쌓이면 계산해요</small></span>')+'</div>';
```
바꾸기:
```js
wthero rhhero"><div><small>최근 '+md(lk)+'</small><b class="num">'+lv+'<i>bpm</i></b>'+(ldf!=null?'<span class="wtdl '+(lst[0]==="bad"?'bad':lst[0]==="ok"?'ok':'')+'">'+(ldf>0?'▲':ldf<0?'▼':'±')+Math.abs(ldf)+'<small> · 2주 평균 '+avg+'bpm</small></span>':'<span class="wtdl"><small>2주 평균은 기록이 쌓이면 계산해요</small></span>')+'</div>'+(ldf!=null?'<span class="rhst '+lst[0]+'">'+lst[1]+'</span>':'')+'</div>';
```

**E16. 식단 카드 머리: 지난주 대비를 큰 숫자 아래 한 줄로**

찾기:
```js
+lw[1]+'<i>점</i></b></div>'+(pw?'<span class="wtchip '+(lw[1]>pw[1]?'ok':lw[1]<pw[1]?'bad':'')+'">'+(lw[1]>pw[1]?'▲':lw[1]<pw[1]?'▼':'±')+Math.abs(lw[1]-pw[1])+' <small>지난주보다</small></span>':'')+'<span class="rhst
```
바꾸기:
```js
+lw[1]+'<i>점</i></b>'+(pw?'<span class="wtdl '+(lw[1]>pw[1]?'ok':lw[1]<pw[1]?'bad':'')+'">'+(lw[1]>pw[1]?'▲':lw[1]<pw[1]?'▼':'±')+Math.abs(lw[1]-pw[1])+'점<small> · 지난주보다</small></span>':'')+'</div><span class="rhst
```

**E17. 훈련 전 컨디션: '41bpm(-6)'을 색 화살표로 (2곳 모두)**

찾기:
```js
(R.diff!=null?'('+(R.diff>0?'+':'')+R.diff+')':'')
```
바꾸기:
```js
(R.diff!=null?' <b class="dlt" style="color:'+(R.diff>0?'var(--bad)':'var(--ok)')+'">'+(R.diff>0?'▲':R.diff<0?'▼':'±')+Math.abs(R.diff)+'</b>':'')
```

**E18. 훈련 전 컨디션: 화살표 색이 보이도록 설명 줄을 그대로 출력 (요약 줄의 컨디션 행)**

찾기:
```js
'</b><span>'+esc(sub)+'</span></button>
```
바꾸기:
```js
'</b><span>'+sub+'</span></button>
```

**E19. 훈련 전 컨디션: 화살표 색이 보이도록 설명 줄을 그대로 출력 (펼친 컨디션 카드)**

찾기:
```js
</b><span>'+esc(sub)+'</span></div></div>'}
```
바꾸기:
```js
</b><span>'+sub+'</span></div></div>'}
```

**E20. 화면 렌더 후 탭 역할 안내 표시**

찾기:
```js
if(tab==="today")vToday(); else if(tab==="plan")vPlan(); else if(tab==="pace")vPace(); else if(tab==="crew")vCrew(); else if(tab==="log")vLog(); else vGuide();
```
바꾸기:
```js
if(tab==="today")vToday(); else if(tab==="plan")vPlan(); else if(tab==="pace")vPace(); else if(tab==="crew")vCrew(); else if(tab==="log")vLog(); else vGuide();
  tabHint();
```

## 6. CSS
### 6-1. 메인 `<style>` 끝에 추가
같은 이름의 규칙이 있으면 아래가 덮어써도 돼.
```css
.card.rwc{padding:0;overflow:hidden;background:var(--card)!important}
.rwh{all:unset;box-sizing:border-box;cursor:pointer;width:100%;display:grid;grid-template-columns:auto 1fr auto 18px;align-items:center;gap:12px;padding:14px 14px 14px 12px;background:linear-gradient(120deg,var(--navy),color-mix(in srgb,var(--accent) 60%,var(--navy)));color:#fff}
.rwh:focus-visible{outline:2px solid var(--accent);outline-offset:-3px}
.rwd{min-width:62px;padding:0 6px;box-sizing:border-box;height:58px;border-radius:16px;display:flex;flex-direction:column;align-items:center;justify-content:center;background:rgba(255,255,255,.13);box-shadow:inset 0 0 0 1px rgba(255,255,255,.2)}
.rwd small{font-size:11px;font-weight:700;opacity:.75;white-space:nowrap}.rwd b{font-size:22px;line-height:1.1;font-family:var(--f-display);white-space:nowrap;letter-spacing:-.02em}
.rwt{display:flex;flex-direction:column;min-width:0;gap:1px}.rwt small{font-size:11.5px;opacity:.75}.rwt b{font-size:16.5px;font-family:var(--f-display);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rwt em{font-style:normal;font-size:12px;font-weight:700;color:#d9d4ff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rwring{width:46px;height:46px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(#b8b1ff calc(var(--p)*1%),rgba(255,255,255,.16) 0)}
.rwring i{width:36px;height:36px;border-radius:50%;display:flex;align-items:baseline;justify-content:center;padding-top:9px;box-sizing:border-box;font-style:normal;font-weight:800;font-size:14px;background:color-mix(in srgb,var(--accent) 45%,var(--navy))}.rwring small{font-size:11px;opacity:.7}
.rwchev{width:8px;height:8px;border-right:2px solid rgba(255,255,255,.75);border-bottom:2px solid rgba(255,255,255,.75);transform:translateY(-2px) rotate(45deg);transition:transform .2s}
.rwh[aria-expanded="true"] .rwchev{transform:translateY(2px) rotate(-135deg)}
.rwbody{padding:14px 14px 14px}
.rwpg{display:flex;align-items:flex-end;justify-content:space-between;gap:10px}.rwpg>div{display:flex;flex-direction:column;min-width:0}.rwpg b{font-size:15px}.rwpg>div small{font-size:12px;color:var(--muted)}
.rwpg>span{font-size:22px;font-weight:800;font-family:var(--f-display);color:var(--accent)}.rwpg>span small{font-size:13px;color:var(--muted);font-weight:700}
.rwpb{height:6px;border-radius:99px;background:var(--bg);overflow:hidden;margin:8px 0 12px}.rwpb u{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--accent),#9ff0be)}
.rwl{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.rwi{position:relative;display:grid;grid-template-columns:38px 1fr 24px;align-items:center;gap:10px;padding:10px 12px 10px 10px;border-radius:14px;background:var(--bg);cursor:pointer;transition:background .15s,box-shadow .15s}
.rwi.now{background:var(--card);box-shadow:inset 0 0 0 1.5px var(--accent),0 3px 12px color-mix(in srgb,var(--accent) 14%,transparent)}
.rwi.late{box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--s2) 55%,transparent)}
.rwi.done{background:color-mix(in srgb,var(--ok) 8%,var(--card))}
.rwin{position:absolute;opacity:0;pointer-events:none}.rwin:focus-visible~.rwck{outline:2px solid var(--accent);outline-offset:2px}
.rwic{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;font-size:19px;background:var(--card)}.rwi.now .rwic{background:var(--accent-soft)}.rwi.done .rwic{filter:grayscale(.6);opacity:.7}
.rwtx{display:flex;flex-direction:column;min-width:0;gap:2px}.rwtt{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.rwtt b{font-size:14px}
.rwtt em{font-style:normal;font-size:11px;font-weight:800;padding:1px 7px;border-radius:99px}.rwnow{background:var(--accent);color:var(--on-accent)}.rwwhen{background:var(--card);color:var(--muted);box-shadow:inset 0 0 0 1px var(--line)}
.rwi.late .rwwhen{color:var(--s2);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--s2) 50%,transparent)}
.rwtx small{font-size:12px;color:var(--muted);line-height:1.45}
.rwi.done .rwtt b{color:var(--muted);text-decoration:line-through;text-decoration-color:color-mix(in srgb,var(--muted) 60%,transparent)}.rwi.done .rwtx small{opacity:.7}
.rwck{width:24px;height:24px;border-radius:50%;box-shadow:inset 0 0 0 2px var(--line);background:var(--card);display:grid;place-items:center}
.rwi.now .rwck{box-shadow:inset 0 0 0 2px var(--accent)}.rwi.done .rwck{background:var(--ok);box-shadow:none}.rwi.done .rwck:after{content:"";width:5px;height:10px;border:solid #fff;border-width:0 2px 2px 0;transform:translateY(-1px) rotate(45deg)}
.rwgo{all:unset;box-sizing:border-box;cursor:pointer;display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:10px;width:100%;margin-top:12px;padding:12px 14px;border-radius:14px;background:linear-gradient(120deg,var(--navy),color-mix(in srgb,var(--accent) 60%,var(--navy)));color:#fff}
.rwgi{font-size:20px}.rwgt{display:flex;flex-direction:column;min-width:0}.rwgt b{font-size:14px}.rwgt small{font-size:12px;opacity:.75;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.rwarr{font-weight:800}
.rwgo:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.wres{margin:2px 14px 10px;animation:fdin .18s ease-out}
.wrhero{display:grid;grid-template-columns:auto 1fr;gap:14px;align-items:center;padding:14px;border-radius:18px;background:linear-gradient(125deg,var(--navy),color-mix(in srgb,var(--accent) 58%,var(--navy)));color:#fff;box-shadow:0 6px 18px color-mix(in srgb,var(--accent) 20%,transparent)}
.wrring>i{width:70px;height:70px;border-radius:50%;display:flex;align-items:baseline;justify-content:center;padding-top:20px;box-sizing:border-box;font-style:normal;position:relative;background:conic-gradient(#9ff0be calc(var(--p)*1%),rgba(255,255,255,.18) 0)}
.wrring>i:before{content:"";position:absolute;inset:7px;border-radius:50%;background:color-mix(in srgb,var(--accent) 30%,var(--navy))}.wrring b,.wrring small{position:relative}.wrring b{font-size:24px;font-family:var(--f-display)}.wrring small{font-size:12px;opacity:.7}
.wrhm{min-width:0;display:flex;flex-direction:column}.wrhm>small{font-size:11.5px;opacity:.75}.wrhm>b{font-size:18px;font-family:var(--f-display);margin:1px 0 6px}
.wrkm{display:flex;justify-content:space-between;font-size:12px;opacity:.9}.wrkm b{font-size:15px}
.wrbar{height:6px;border-radius:99px;background:rgba(255,255,255,.18);overflow:hidden;margin-top:4px}.wrbar u{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#9ff0be,#cfc9ff)}
.wrst{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:8px}.wrst>div{display:flex;flex-direction:column;align-items:center;gap:1px;padding:9px 4px 8px;border-radius:14px;background:var(--bg)}
.wrst>div>span{font-size:16px}.wrst small{font-size:11px;color:var(--muted);font-weight:700}.wrst b{font-size:18px;font-family:var(--f-display)}.wrst i{font-style:normal;font-size:11px;color:var(--muted);margin-left:1px}
.wrdays{display:grid;grid-template-columns:repeat(7,1fr);gap:6px;margin-top:8px;padding:10px 8px 8px;border-radius:14px;background:var(--bg)}
.wrd{display:flex;flex-direction:column;align-items:center;gap:4px}.wrv{font-size:12px;font-weight:800;min-height:16px;color:var(--muted)}
.wrcol{width:16px;height:46px;border-radius:99px;background:var(--card);display:flex;align-items:flex-end;overflow:hidden}.wrcol u{display:block;width:100%;border-radius:99px;background:var(--ok)}
.wrd.miss .wrcol{box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--s1) 70%,transparent)}.wrd.miss .wrcol u{background:repeating-linear-gradient(135deg,color-mix(in srgb,var(--s1) 22%,transparent) 0 4px,transparent 4px 8px)}.wrd.miss .wrv{color:var(--s1)}
.wrd.rest .wrcol{opacity:.55}.wrd small{font-size:11px;font-weight:700;color:var(--muted)}.wrd.wpt small{color:var(--accent)}.wrd small .kstar{font-style:normal;font-size:11px;margin-right:1px;vertical-align:1px}
.wrbest{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center;margin-top:8px;padding:10px 12px;border-radius:14px;background:color-mix(in srgb,var(--s3) 12%,var(--card));box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--s3) 30%,transparent)}
.wrmed{font-size:22px}.wrbt{min-width:0;display:flex;flex-direction:column}.wrbt small{font-size:11px;color:var(--muted)}.wrbt b{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.wrbs{font-size:22px;font-family:var(--f-display)}
.wrai{all:unset;box-sizing:border-box;cursor:pointer;display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center;width:100%;margin-top:8px;padding:11px 14px;border-radius:14px;background:var(--accent-soft);color:var(--accent)}
.wrai span:nth-child(2){display:flex;flex-direction:column}.wrai b{font-size:13.5px}.wrai small{font-size:11.5px;color:var(--muted)}.wrarr{font-weight:800}.wrai:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.wdayb{all:unset;box-sizing:border-box;cursor:pointer;display:grid;grid-template-columns:auto auto 1fr 22px;align-items:center;gap:8px;width:calc(100% - 28px);margin:0 14px 12px;padding:10px 12px;border-radius:12px;box-shadow:inset 0 0 0 1px var(--line);background:var(--card)}
.wdayb b{font-size:13.5px}.wdayb .wrsum{font-size:12px;color:var(--muted);text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.wdayb[aria-expanded="true"] .chev::before{transform:translateY(2px) rotate(-135deg)}.wdayb:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.pastwk{border:1px solid var(--line);border-radius:16px;background:var(--card);overflow:hidden}
.pastwk>summary.pwh{display:grid;grid-template-columns:auto 1fr auto 22px;align-items:center;gap:10px;padding:12px 14px;cursor:pointer}
.pwi{width:36px;height:36px;border-radius:11px;display:grid;place-items:center;background:var(--bg);font-size:18px}.pwt{display:flex;flex-direction:column;min-width:0}.pwt b{font-size:15px}.pwt small{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pwo{font-size:12px;font-weight:700;color:var(--accent)}.pastwk[open]{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent);background:color-mix(in srgb,var(--accent) 6%,var(--card));padding-bottom:8px}.pastwk[open]>summary.pwh{border-bottom:1px dashed color-mix(in srgb,var(--accent) 35%,var(--line))}
.pastwk>.wk{box-shadow:none}
.wtdl{display:block;margin-top:3px;font-size:12.5px;font-weight:800;color:var(--muted)}.wtdl.ok{color:var(--ok)}.wtdl.bad{color:var(--bad)}.wthero .wtdl small{display:inline;font-weight:600;color:var(--muted);font-size:11px}
.bseg .bk .bks{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.bseg .bk .bks .dlt{font-size:11px;font-weight:800;margin-left:1px}
:root{--r-card:16px;--r-ctl:12px;--r-pill:99px;--sp-1:4px;--sp-2:8px;--sp-3:12px;--sp-4:16px;--sh-1:0 1px 3px rgba(20,20,60,.06);--sh-2:0 4px 16px rgba(20,20,60,.08)}
.card,.session,.strip,.wk{border-radius:var(--r-card)}
.strip .srow .sbtn:not(.on){border:0;background:var(--accent-soft);color:var(--accent)}
.strip .srow .sbtn.on{border:0;background:var(--accent);color:var(--on-accent)}
.sbtn{min-height:32px;border-radius:var(--r-pill)}
.btn{border-radius:var(--r-ctl)}
.nudge{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center;padding:12px 14px;border-radius:var(--r-card);background:color-mix(in srgb,var(--accent) 8%,var(--card));box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--accent) 22%,transparent)}
.nudge>span:first-child{font-size:22px}.nudge>span:nth-child(2){display:flex;flex-direction:column;min-width:0}.nudge b{font-size:13.5px}.nudge small{font-size:12px;color:var(--muted)}.nudge .btn{padding:8px 14px}
.dlt{font-weight:800}
.tabhint{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:var(--r-ctl);background:var(--accent-soft);font-size:12.5px;color:var(--ink)}.tabhint span{flex:1;min-width:0}.tabhint b{color:var(--accent);margin-right:6px}
.tabhint button{border:0;border-radius:var(--r-pill);padding:6px 12px;background:var(--accent);color:var(--on-accent);font-weight:700;font-size:12px;cursor:pointer}
header.top{position:sticky}header.top::after{content:"";position:absolute;left:0;right:0;bottom:-3px;height:3px;background:linear-gradient(90deg,transparent,var(--accent),transparent);background-size:40% 100%;background-repeat:no-repeat;opacity:0;transition:opacity .2s}
body.busy header.top::after{opacity:1;animation:busybar 1.1s linear infinite}@keyframes busybar{from{background-position:-40% 0}to{background-position:140% 0}}
@media (prefers-reduced-motion:reduce){body.busy header.top::after{animation:none;background-size:100% 100%;opacity:.6}}
.resact .aibtn{display:flex;align-items:center;gap:4px;padding:3px 10px;font-size:12px;font-weight:600;border:0;background:var(--accent-soft);color:var(--accent)}
.pastwk[open]>summary .chev::before{transform:translateY(2px) rotate(-135deg)}
.pastwk>.wk{margin:8px 8px 0}
```
### 6-2. 작은 글자 없애기
메인 `<style>`과 HTML 문자열 안의 `font-size:9px`, `font-size:9.5px`, `font-size:10px`, `font-size:10.5px`를 모두 `font-size:11px`로 바꿔.

### 6-3. PC 화면
PC에서도 모바일과 같은 한 줄 화면이야. `@media (min-width:1000px)`로 오늘 탭을 2단으로 만들거나 머리줄·본문을 1080px로 넓히는 규칙이 있으면 지워. 식단 카드를 PC에서만 처음부터 펼치는 `window.innerWidth>=1000` 조건도 `false`로 바꿔.

## 7. 확인하고 알려줘
1. 문법 오류 없이 페이지가 열리는지
2. 오늘 탭 순서: (탭 안내) → 요약 줄 → 날짜 줄 → 오늘 훈련 카드. 대회 주 카드는 주간 띠 아래, 새 소식은 맨 아래.
3. 훈련표: 지난 주 카드를 펼치면 지난 주가 카드 안에 묶이는지. 끝난 주를 펼치면 결과 카드와 '일별 기록' 버튼이 나오는지. 이번 주에는 결과가 안 나오는지.
4. 바디 탭 맨 위 지표가 '목표까지 ▼…', '평소 ▼…' 같은 색 화살표로 나오는지
5. 대회 7일 전부터 대회 주 카드가 'D-6' 한 줄 배지와 '지금' 강조로 나오는지
6. 게시 후 저장된 권한 선언이 이전과 같은지
