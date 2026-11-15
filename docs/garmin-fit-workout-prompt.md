# 작업 지시: 가민 워크아웃 파일(.fit) 받기 + 가이드 추가

> 아래 전체를 복사해서, **복제한 'Sub-3 훈련 체크' 앱을 만든 Claude 대화**에 붙여 넣으세요.
> 맨 아래 `<여기에 내 앱 링크>` 자리에 내 앱(복제본) 링크를 넣어 주세요.

---

내 아티팩트 "Sub-3 훈련 체크"에 **가민 워크아웃 파일 받기** 기능과 가이드를 추가해서 **같은 URL에 다시 게시**해 줘.

- 아티팩트: <여기에 내 앱 링크>

## 0. 먼저 지킬 것
1. `artifact-capabilities` 스킬과 `artifact-design` 스킬을 먼저 로드해.
2. 아티팩트를 `read`로 읽고, **그 원본 파일을 고쳐서** 같은 `url`로 재게시해. 새 아티팩트를 만들지 마.
3. **권한 선언(capabilities)은 건드리지 마.** 게시할 때 `capabilities`를 빼서 기존 선언을 그대로 이어가.
   - 단, 저장된 선언에 `downloads`가 없으면 그때만 넣어. 기존 선언 전체를 한 글자도 바꾸지 말고 `"downloads": true`만 더해서 전체 세트로 다시 써.
   - `contract`는 지정하지 마.
4. **COROS 관련 기능은 그대로 둬.** 이번 기능은 워치 연결이 가민·애플(`S.watch==="freddy"`)일 때만 동작해야 해.
5. 기존 코드와 이름이 같은 함수가 이미 있으면 아래 코드로 **교체**하고, 중복 정의가 남지 않게 해.
6. 앱 버전 값(`APP_VERSION`, `APP_RELEASED`, `VLOG`)이 있으면 **바꾸지 마.** 복제한 앱의 버전은 복제 시점 값으로 고정해서 관리해.

## 1. 기능 요약
- **오늘 탭**: 워치 연결이 가민·애플일 때, 훈련 카드의 '💬 코치 팁' 옆에 **⌚ 가민 파일 받기** 버튼을 넣어.
  - 누르면 그날 훈련을 가민 워크아웃 파일(.fit)로 만들어 zip으로 저장해. (.fit은 claude.ai 저장 허용 확장자가 아니라서 zip으로 감싸.)
  - 상태 표시: `⌚ 가민 파일 받기`(안 받음) / `⌚ 바뀜 · 다시 받기`(받은 뒤 계획 변경) / `⌚ 받음 ✓`
  - 받은 직후 버튼 아래에 **워치에 넣는 4단계 안내 상자**를 띄워. ✕로 닫을 수 있어.
- **훈련표 탭**: 맨 위 요약 줄(툴바)에 **⌚ 가민 워크아웃 파일 [받기]** 줄을 추가해.
  - 패널에서 1·2·4주를 고르고 날짜를 체크하면 .fit 여러 개를 zip 하나로 받아.
  - 받은 뒤에는 같은 4단계 안내 상자를 보여줘.
- **가이드**: 훈련표 그룹에 **'가민 워크아웃 파일'** 카드를 추가해. USB로 넣는 방법도 포함해.
- **기록**: 받은 날은 `S.gfit[날짜]={h:해시,at:시각}`로 저장해. 계획이 바뀌면 해시가 달라져서 '바뀜'으로 표시돼.

## 2. 앱에 이미 있어야 하는 것 (확인만)
아래 전역 이름은 원래 앱에 있어. 없거나 이름이 다르면 맞춰 줘.
- `S`, `save()`, `keepIf()`, `toast()`, `view`, `esc()`
- `T`, `START`, `TOTAL`, `K`, `ZONES`, `planAt()`, `idxOf()`, `addDays()`, `md()`, `p()`, `DOW`, `ptxt()`, `parseTime()`, `fmtMS()`
- `pwrap()`, `bindPclose()`, `planStrip()`, `bindPlanStrip()`, `info()`

## 3. 추가할 JavaScript (메인 스크립트, `function planStrip(` 바로 앞)
```js
var FITB={};
var FITNEXT="";
var GM={range:14,pick:null,busy:false,msg:""};
function cwRange(lo,hi){var a=Math.round(lo*K),b=Math.round((hi==null?lo+40:hi)*K);a=Math.max(120,Math.min(1499,a));b=Math.max(120,Math.min(1499,b));return a<=b?[a,b]:[b,a]}
function cwZ(k){var z=ZONES.filter(function(x){return x.k===k})[0];return z?cwRange(z.lo,z.hi):cwRange(330,370)}
function cwP(s){var m=String(s).split(":"),v=+m[0]*60+ +m[1];return cwRange(v-3,v+3)}
function cwSec(st,v,time,r){return{sectionType:st,targetType:time?2:1,targetValue:Math.max(1,Math.round(v)),intensityType:2,intensityValueStart:r[0],intensityValueEnd:r[1]}}
function cwM(n,u){n=parseFloat(n);return /K|km/i.test(u)?n*1000:n}
function cwKey(s){s=s||"";return /^WU/.test(s)?"wu":/^CD/.test(s)?"cd":/^MP/.test(s)?"mp":/^(E|이지)\b|^이지/.test(s)?"e":/조깅/.test(s)?"rec":/LSD/.test(s)?"lsd":/지속주/.test(s)?"steady":/템포/.test(s)?"tempo":null}
function cwCourse(t,ds){
  if(!t||t[0]==="r"||t[0]==="x")return null;
  var L=String(t[1]).split("\n").map(function(x){return x.trim()}).filter(Boolean),sec=[],skip=[],str=false,vb=null;
  for(var i=0;i<L.length;i++){var s=L[i],nx=L[i+1]||"",m;
    if(/^총 /.test(s)||/^휴식/.test(s))continue;
    if(/근력/.test(s)){str=true;continue}
    if(/^\(.*\)$/.test(s))continue;
    if((m=s.match(/스트라이드(?:\s*(\d+)\s*회)?/))){str=str||false;sec.push({intervalGroup:true,repeats:Math.min(20,+(m[1]||4)),sets:[cwSec(2,100,false,cwZ("int")),cwSec(3,100,false,cwZ("rec"))]});continue}
    if((m=s.match(/(하프|10K)\s*기록측정/))){var dm=m[1]==="하프"?21097:10000,tm=(nx.match(/(\d{1,2}:\d{2}(?::\d{2})?)\s*이내/)||[])[1],ts=tm?parseTime(tm):null;
      if(ts){var pc=ts*K/(dm/1000);sec.push(cwSec(2,dm,false,[Math.max(120,Math.round(pc-4)),Math.round(pc)]));i++}else sec.push(cwSec(2,dm,false,cwZ("tempo")));continue}
    if((m=s.match(/^변속\s*(\d+(?:\.\d+)?)\s*K/))){vb=+m[1]*1000;continue}
    if(vb&&/교대/.test(s)){var seg=[],re=/(\d+(?:\.\d+)?)\s*(K|m)\s*@(\d:\d\d)/g,q;while((q=re.exec(s)))seg.push([cwM(q[1],q[2]),q[3]]);
      if(seg.length>=2){var per=seg.reduce(function(a,x){return a+x[0]},0);sec.push({intervalGroup:true,repeats:Math.max(1,Math.min(20,Math.round(vb/per))),sets:seg.map(function(x,j){return cwSec(j===0?2:3,x[0],false,cwP(x[1]))})})}vb=null;continue}
    if((m=s.match(/^(?:(MP|템포|언덕)\s*)?(\d+(?:\.\d+)?)\s*(K|m)\s*(?:@(\d:\d\d))?\s*[×x]\s*(\d+)/))){
      var wr=m[4]?cwP(m[4]):m[1]==="MP"?cwZ("mp"):m[1]==="템포"?cwZ("tempo"):cwZ("int"),wd=cwM(m[2],m[3]),rd=400,rz=cwZ("rec"),rq;
      if(/^\(.*\)$/.test(nx)){if((rq=nx.match(/(\d+(?:\.\d+)?)\s*(K|m)\s*(조깅|플로트)/)))rd=cwM(rq[1],rq[2]);if(/플로트/.test(nx))rz=cwZ("float");if(/내리막/.test(nx))rd=wd;i++}
      sec.push({intervalGroup:true,repeats:Math.max(1,Math.min(20,+m[5])),sets:[cwSec(2,wd,false,wr),cwSec(3,rd,false,rz)]});continue}
    if((m=s.match(/(LSD|조깅)\s*(\d+)\s*분/))){sec.push(cwSec(2,+m[2]*60,true,cwZ(m[1]==="LSD"?"lsd":"rec")));continue}
    if((m=s.match(/^(?:\d세트\s*)?([^\d@]*?)\s*(\d+(?:\.\d+)?)\s*K(?:\s*@(\d:\d\d))?/))){var kw=m[1].trim(),k=cwKey(kw);
      if(!k&&!m[3]){skip.push(s);continue}
      sec.push(cwSec(k==="wu"?1:k==="cd"?4:2,+m[2]*1000,false,m[3]?cwP(m[3]):cwZ(k)));continue}
    skip.push(s)}
  if(!sec.length)return null;
  var nm=ptxt(t[1]).split("\n").map(function(x){return x.trim()}).filter(function(x){return x&&!/^\(.*\)$/.test(x)}).join(" · ").slice(0,100);
  var d=md(ds)+" ("+DOW[(new Date(p(ds)).getUTCDay()+6)%7]+") Sub-3 훈련 체크 계획: "+ptxt(t[1]).replace(/\n+/g," / ")+". 페이스는 내 기준 설정에 맞춘 실제 페이스 범위예요."+(str?" 달린 뒤 근력 운동도 잊지 마세요.":"");
  return{course:{sportType:1,courseName:nm||"러닝",courseDescription:d.slice(0,900),sections:sec},skip:skip};
}
function cwHash(c){var s=JSON.stringify(c),h=0;for(var i=0;i<s.length;i++){h=(h*31+s.charCodeAt(i))|0}return String(h)}
function cwDates(n){var out=[],last=addDays(START,TOTAL-1);for(var j=0;j<n;j++){var ds=addDays(T,j);if(ds>last)break;out.push(ds)}return out}
function cwPlanOf(ds){var i=idxOf(ds);return i>=0&&i<TOTAL?planAt(i):null}
function cwSecTxt(c){function one(x){var r=x.intensityValueStart===x.intensityValueEnd?fmtMS(x.intensityValueStart):fmtMS(x.intensityValueStart)+"~"+fmtMS(x.intensityValueEnd),v=x.targetType===2?Math.round(x.targetValue/60)+"분":x.targetValue>=1000?(Math.round(x.targetValue/100)/10)+"K":x.targetValue+"m";return v+" "+r}
  return c.sections.map(function(x){return x.intervalGroup?"("+x.sets.map(one).join(" + ")+") ×"+x.repeats:one(x)}).join(" → ")}
function cwSegs(c){var out=[];function col(x){if(x.sectionType===3)return "var(--rec)";var a=(x.intensityValueStart+x.intensityValueEnd)/2/K;return a<235?"var(--bad)":a<250?"var(--taper)":a<262?"var(--q)":a<300?"var(--build)":x.sectionType===1||x.sectionType===4?"var(--rec)":"var(--base)"}
  function w(x){return x.targetType===2?x.targetValue*1000/360:x.targetValue}
  c.sections.forEach(function(x){if(x.intervalGroup){for(var r=0;r<x.repeats;r++)x.sets.forEach(function(y){out.push([w(y),col(y)])})}else out.push([w(x),col(x)])});
  var tot=out.reduce(function(a,x){return a+x[0]},0)||1;
  return '<span class="cwbar" aria-hidden="true">'+out.slice(0,80).map(function(x){return '<i style="flex:'+Math.max(0.6,x[0]/tot*100).toFixed(2)+';background:'+x[1]+'"></i>'}).join("")+'</span>'}
function cwKm(c){var m=0,s=0;c.sections.forEach(function(x){(x.intervalGroup?x.sets.map(function(y){return [y,x.repeats]}):[[x,1]]).forEach(function(q){if(q[0].targetType===2)s+=q[0].targetValue*q[1];else m+=q[0].targetValue*q[1]})});
  return (m?(Math.round(m/100)/10)+"km":"")+(m&&s?" + ":"")+(s?Math.round(s/60)+"분":"")}
function fitCrc(crc,b){var T=[0x0000,0xCC01,0xD801,0x1400,0xF001,0x3C00,0x2800,0xE401,0xA001,0x6C00,0x7800,0xB401,0x5000,0x9C01,0x8801,0x4400];
  var t=T[crc&0xF];crc=(crc>>4)&0x0FFF;crc=crc^t^T[b&0xF];t=T[crc&0xF];crc=(crc>>4)&0x0FFF;return crc^t^T[(b>>4)&0xF]}
function fitAscii(s){s=String(s).replace(/^총\s*/,"");[[/스트라이드\s*(\d+)\s*회/g,"Strides x$1"],[/스트라이드/g,"Strides"],[/(\d+)\s*회/g,"x$1"],[/(\d+)\s*분/g,"$1min"],[/가벼운\s*근력|근력/g,"+Strength"],[/회복\s*조깅|조깅/g,"Jog"],[/회복/g,"Rec"],[/이지/g,"E"],[/지속주/g,"Steady"],[/템포/g,"Tempo"],[/변속/g,"Fartlek"],[/언덕/g,"Hill"],[/하프/g,"Half"],[/기록측정/g,"TT"],[/리허설/g,"Rehearsal"],[/장거리/g,"Long"]].forEach(function(r){s=s.replace(r[0],r[1])});return s.replace(/[·•]/g," ").replace(/[^A-Za-z0-9 .@:\/+\-x×~]/g," ").replace(/×/g,"x").replace(/\s+/g," ").trim()}
function fitName(c,ds){return (ds.slice(5,7)+ds.slice(8)+" "+fitAscii(c.courseName)).slice(0,31).trim()||"Run"}
function fitBuild(c,ds){
  var out=[];function u8(v){out.push(v&255)}function u16(v){u8(v);u8(v>>8)}function u32(v){v=v>>>0;u8(v);u8(v>>>8);u8(v>>>16);u8(v>>>24)}
  function str(s,n){for(var i=0;i<n;i++)u8(i<s.length?s.charCodeAt(i):0)}
  function def(loc,gl,fs){u8(0x40|loc);u8(0);u8(0);u16(gl);u8(fs.length);fs.forEach(function(f){u8(f[0]);u8(f[1]);u8(f[2])})}
  var steps=[],idx=0;
  function spd(p){return Math.round(1000/p*1000)}
  function plain(x){var it={1:2,2:0,3:4,4:3}[x.sectionType]||0,st={i:idx++,dt:x.targetType===2?0:1,dv:x.targetType===2?x.targetValue*1000:x.targetValue*100,tt:0,tv:0,lo:0,hi:0,it:it};
    if(x.intensityType===2){st.lo=spd(Math.max(x.intensityValueStart,x.intensityValueEnd));st.hi=spd(Math.min(x.intensityValueStart,x.intensityValueEnd))}else st.tt=2;steps.push(st)}
  c.sections.forEach(function(x){if(x.intervalGroup){var from=idx;x.sets.forEach(plain);steps.push({i:idx++,dt:6,dv:from,tt:2,tv:x.repeats,lo:0,hi:0,it:0})}else plain(x)});
  var nm=fitName(c,ds);
  // file_id
  def(0,0,[[0,1,0x00],[1,2,0x84],[2,2,0x84],[3,4,0x8C],[4,4,0x86]]);
  u8(0);u8(5);u16(255);u16(0);u32(Math.floor(Date.now()/1000)%4294967295||1);u32(Math.floor((Date.now()-631065600000)/1000));
  // workout
  def(1,26,[[4,1,0x00],[5,4,0x8C],[6,2,0x84],[8,32,0x07]]);
  u8(1);u8(1);u32(32);u16(steps.length);str(nm,32);
  // workout_step
  def(2,27,[[254,2,0x84],[1,1,0x00],[2,4,0x86],[3,1,0x00],[4,4,0x86],[5,4,0x86],[6,4,0x86],[7,1,0x00]]);
  steps.forEach(function(s){u8(2);u16(s.i);u8(s.dt);u32(s.dv);u8(s.tt);u32(s.tv);u32(s.lo);u32(s.hi);u8(s.it)});
  var hdr=[14,0x10];var pv=2132;hdr.push(pv&255,pv>>8);var n=out.length;hdr.push(n&255,(n>>8)&255,(n>>16)&255,(n>>>24)&255,46,70,73,84);
  var hc=0;hdr.forEach(function(b){hc=fitCrc(hc,b)});hdr.push(hc&255,hc>>8);
  var all=hdr.concat(out),cc=0;all.forEach(function(b){cc=fitCrc(cc,b)});all.push(cc&255,cc>>8);
  return new Uint8Array(all)}
function isGarmin(){return S.watch==="freddy"}
function zipFiles(files){
  var ct=[];for(var n=0;n<256;n++){var c=n;for(var k=0;k<8;k++)c=c&1?0xEDB88320^(c>>>1):c>>>1;ct[n]=c>>>0}
  var o=[],cen=[];function w16(a,v){a.push(v&255,(v>>8)&255)}function w32(a,v){a.push(v&255,(v>>>8)&255,(v>>>16)&255,(v>>>24)&255)}
  files.forEach(function(f){var d=f.data,crc=0xFFFFFFFF;for(var i=0;i<d.length;i++)crc=ct[(crc^d[i])&255]^(crc>>>8);crc=(crc^0xFFFFFFFF)>>>0;
    var nb=new TextEncoder().encode(f.name),off=o.length;
    w32(o,0x04034b50);w16(o,20);w16(o,0);w16(o,0);w16(o,0);w16(o,0x21);w32(o,crc);w32(o,d.length);w32(o,d.length);w16(o,nb.length);w16(o,0);nb.forEach(function(b){o.push(b)});for(var j=0;j<d.length;j++)o.push(d[j]);
    var c2=[];w32(c2,0x02014b50);w16(c2,20);w16(c2,20);w16(c2,0);w16(c2,0);w16(c2,0);w16(c2,0x21);w32(c2,crc);w32(c2,d.length);w32(c2,d.length);w16(c2,nb.length);w16(c2,0);w16(c2,0);w16(c2,0);w16(c2,0);w32(c2,0);w32(c2,off);nb.forEach(function(b){c2.push(b)});cen=cen.concat(c2)});
  var cd=o.length;o=o.concat(cen);w32(o,0x06054b50);w16(o,0);w16(o,0);w16(o,files.length);w16(o,files.length);w32(o,cen.length);w32(o,cd);w16(o,0);
  return new Uint8Array(o)}
function zipStore(name,data){return zipFiles([{name:name,data:data}])}
function fitHowHtml(open){return '<details class="fithow"'+(open?' open':'')+'><summary><span>📲 워치에 넣는 방법 (USB)</span><span class="chev" aria-hidden="true"></span></summary><ol>'
  +'<li><span><b>zip 풀기</b> — 받은 zip을 PC에서 풀면 날짜별 <b>.fit</b> 파일이 나와요. 휴대폰에서 받았다면 카톡 나에게 보내기·메일·클라우드로 PC에 옮겨요</span></li>'
  +'<li><span><b>워치 연결</b> — 충전 케이블로 워치를 PC에 연결해요. 윈도우는 탐색기에 <b>GARMIN</b>이 보이고, Mac은 <b>OpenMTP</b> 같은 무료 프로그램으로 열어요</span></li>'
  +'<li><span><b>복사</b> — 워치 안의 <b>GARMIN → NewFiles</b> 폴더에 .fit 파일을 모두 복사해요</span></li>'
  +'<li><span><b>확인</b> — 케이블을 빼면 워치가 파일을 읽어요. 워치의 <b>훈련 → 워크아웃</b>(모델에 따라 러닝 시작 화면 → 옵션 → 훈련)에서 날짜 이름의 워크아웃을 골라 뛰어요</span></li>'
  +'</ol><p>계획이 바뀌어 다시 받으면 같은 이름이 하나 더 생길 수 있어요. 예전 것은 워치 워크아웃 목록에서 지워 주세요.</p></details>'}
function fitNextHtml(n){return '<div class="fitnext" role="status"><div class="fitnh"><b>✓ '+(n>1?n+"일 파일을":"파일을")+' 받았어요 · 워치에 넣기</b><button type="button" class="fitnx" data-fitnx aria-label="닫기">✕</button></div><ol><li><span>PC에서 <b>zip 풀기</b> (휴대폰에서 받았으면 PC로 옮겨요)</span></li><li><span>워치를 <b>케이블로 PC에 연결</b> (Mac은 OpenMTP)</span></li><li><span><b>GARMIN → NewFiles</b> 폴더에 .fit 복사</span></li><li><span>케이블을 빼면 워치 <b>훈련 → 워크아웃</b>에 생겨요</span></li></ol></div>'}
function gmCard(){
  var m=S.gfit||{},days=cwDates(GM.range).map(function(ds){var t=cwPlanOf(ds),c=t&&cwCourse(t,ds);if(!c)return null;var h=cwHash(c.course),r=m[ds];return {ds:ds,c:c,h:h,st:!r?"new":r.h===h?"same":"chg"}}).filter(Boolean);
  if(!GM.pick){GM.pick={};days.forEach(function(d){if(d.st!=="same")GM.pick[d.ds]=true})}
  var dw=function(ds){return DOW[(new Date(p(ds)).getUTCDay()+6)%7]},ds0=cwDates(GM.range),np=days.filter(function(d){return GM.pick[d.ds]}).length,nNew=days.filter(function(d){return d.st==="new"}).length,nChg=days.filter(function(d){return d.st==="chg"}).length;
  var h='<div class="card cwcard gmcard"><div class="whead" style="padding-right:0"><span class="wico" aria-hidden="true">⌚</span><div style="flex:1;min-width:0"><h2>가민 워크아웃 파일</h2><small>골라서 zip으로 받아 USB로 워치에 넣어요</small></div></div>';
  h+='<div class="cwtool"><span class="pseg cwseg" role="radiogroup" aria-label="기간" style="--pn:3;--pi:'+[7,14,28].indexOf(GM.range)+'"><span class="pind" aria-hidden="true"></span>'+[[7,"1주"],[14,"2주"],[28,"4주"]].map(function(x){return '<button type="button" role="radio" data-gmr="'+x[0]+'" aria-checked="'+(GM.range===x[0])+'">'+x[1]+'</button>'}).join("")+'</span><span class="cwrange num">'+(ds0.length?md(ds0[0])+' ~ '+md(ds0[ds0.length-1]):'')+'</span></div>';
  h+='<div class="cwhero gmhero">'+[[nNew,"아직 안 받음","ok"],[nChg,"계획 바뀜","mid"],[days.length-nNew-nChg,"받은 그대로","na"]].map(function(x){return '<div class="'+x[2]+(x[0]?'':' zero')+'"><b class="num">'+x[0]+'</b><small>'+x[1]+'</small></div>'}).join("")+'</div>';
  if(!days.length)h+='<div class="cwdone">이 기간엔 받을 훈련이 없어요</div>';
  else{var allOn=days.every(function(d){return GM.pick[d.ds]});
    h+='<div class="cwgh"><b>훈련 <span class="num">'+days.length+'</span></b><button type="button" class="linkbtn" data-gmall="'+(allOn?0:1)+'">'+(allOn?'모두 해제':'모두 선택')+'</button></div><div class="cwgrp">'+days.map(function(d){var on=!!GM.pick[d.ds],L={new:["새 파일","ok"],chg:["바뀜","mid"],same:["받음","na"]}[d.st],cs=d.c.course.sections,one=cs.length===1&&!cs[0].intervalGroup;
      return '<label class="cwi'+(on?' on':'')+'"><input type="checkbox" class="cwck" data-gmp="'+d.ds+'"'+(on?' checked':'')+'><span class="cwtick" aria-hidden="true"></span><span class="cwdate"><b class="num">'+(+d.ds.slice(8))+'</b><small>'+(+d.ds.slice(5,7))+'월 · '+dw(d.ds)+'</small></span><span class="cwbody"><span class="cwtop"><b>'+esc(d.c.course.courseName)+'</b><span class="rhst '+L[1]+'">'+L[0]+'</span></span>'+cwSegs(d.c.course)+'<small class="cwsub">'+(one?'':'<b class="num">'+cwKm(d.c.course)+'</b> · ')+esc(cwSecTxt(d.c.course))+'</small><small class="cwfrom">워치 이름: '+esc(fitName(d.c.course,d.ds))+'</small></span></label>'}).join("")+'</div>';
    h+='<div class="cwfoot"><span class="cwfmsg">'+(GM.msg?esc(GM.msg):np?'<b class="num">'+np+'</b>일 · .fit 파일 '+np+'개를 zip 하나로':'받을 날을 골라 주세요')+'</span><button class="btn" id="gm-dl" style="background:var(--accent);color:var(--on-accent)"'+(np&&!GM.busy?'':' disabled')+'>'+(GM.busy?'만드는 중…':'⌚ '+np+'일 파일 받기')+'</button></div>'}
  if(GM.done)h+=fitNextHtml(GM.done);
  h+=fitHowHtml(!Object.keys(m).length&&!GM.done)+'</div>';
  return h}
async function gmDownload(){var list=cwDates(GM.range).filter(function(ds){return GM.pick&&GM.pick[ds]}).map(function(ds){var c=cwCourse(cwPlanOf(ds),ds);return c?{ds:ds,c:c}:null}).filter(Boolean);if(!list.length||GM.busy)return;
  GM.busy=true;GM.msg="";keepIf();
  try{var files=list.map(function(x){return {name:"sub3-"+x.ds.replace(/-/g,"")+".fit",data:fitBuild(x.c.course,x.ds)}}),zip=zipFiles(files),fn="sub3-garmin-"+list[0].ds.replace(/-/g,"").slice(4)+(list.length>1?"-"+list[list.length-1].ds.replace(/-/g,"").slice(4):"")+".zip";
    var ok=await fitSave(fn,zip);if(ok){S.gfit=S.gfit||{};list.forEach(function(x){S.gfit[x.ds]={h:cwHash(x.c.course),at:Date.now()}});save();GM.pick=null;GM.done=list.length;GM.msg=""}}
  catch(e){GM.msg="파일을 만들지 못했어요"}finally{GM.busy=false;keepIf()}}
async function fitSave(fn,zip){var dl=window.claude&&window.claude.use?await window.claude.use("downloads").catch(function(){return null}):null;
  if(dl){try{await dl.save({filename:fn,data:new Blob([zip],{type:"application/zip"})});return true}catch(e){if(e&&e.code!=="declined")toast("파일을 저장하지 못했어요"+(e&&e.code?" ("+e.code+")":""));return false}}
  var a=document.createElement("a");a.href=URL.createObjectURL(new Blob([zip],{type:"application/zip"}));a.download=fn;document.body.appendChild(a);a.click();setTimeout(function(){URL.revokeObjectURL(a.href);a.remove()},1000);return true}
function bindGm(){var c=view.querySelector(".gmcard");if(!c)return;
  c.querySelectorAll("[data-gmr]").forEach(function(b){b.onclick=function(){GM.range=+b.dataset.gmr;GM.pick=null;GM.msg="";GM.done=0;keepIf()}});
  c.querySelectorAll("[data-gmall]").forEach(function(b){b.onclick=function(){var on=b.dataset.gmall==="1";cwDates(GM.range).forEach(function(ds){if(cwCourse(cwPlanOf(ds),ds))GM.pick[ds]=on});GM.msg="";keepIf()}});
  c.querySelectorAll("[data-gmp]").forEach(function(x){x.onchange=function(){GM.pick[x.dataset.gmp]=x.checked;GM.msg="";keepIf()}});
  var d=c.querySelector("#gm-dl");if(d)d.onclick=gmDownload;
  c.querySelectorAll("[data-fitnx]").forEach(function(b){b.onclick=function(){GM.done=0;keepIf()}})}
function fitDayBtn(ds,t){if(ds<T)return "";var c=cwCourse(t,ds);if(!c)return "";var r=(S.gfit||{})[ds],st=!r?"new":r.h===cwHash(c.course)?"same":"chg",bz=FITB.busy===ds;
  return '<button type="button" class="aiq1 cwq fitq '+st+'" data-fitdl="'+ds+'"'+(FITB.busy?' disabled':'')+' title="가민 워크아웃 파일(.fit)을 zip으로 받아 USB로 워치 GARMIN/NewFiles 폴더에 넣어요">'+(bz?'⌚ 만드는 중…':st==="new"?'⌚ 가민 파일 받기':st==="chg"?'⌚ 바뀜 · 다시 받기':'⌚ 받음 ✓')+'</button>'}
async function fitDownload(ds){var t=cwPlanOf(ds),c=cwCourse(t,ds);if(!c||FITB.busy)return;FITB.busy=ds;keepIf();
  try{var base="sub3-"+ds.replace(/-/g,""),ok=await fitSave(base+"-garmin.zip",zipStore(base+".fit",fitBuild(c.course,ds)));
    if(ok){S.gfit=S.gfit||{};S.gfit[ds]={h:cwHash(c.course),at:Date.now()};save();FITNEXT=ds}}
  catch(e){toast("가민 파일을 만들지 못했어요")}finally{FITB.busy="";keepIf()}}
```

## 4. 추가할 CSS (메인 `<style>` 끝)
이미 같은 규칙이 있으면 중복은 빼도 돼.
```css
.pseg{position:relative;display:grid;grid-template-columns:repeat(var(--pn,3),1fr);gap:0;padding:4px;border-radius:16px;background:var(--card);box-shadow:var(--sh)}
.pseg .pind{position:absolute;top:4px;bottom:4px;left:4px;width:calc((100% - 8px)/var(--pn,3));border-radius:12px;background:var(--accent-soft);box-shadow:inset 0 0 0 1.5px var(--accent);transform:translateX(calc(var(--pi)*100%));transition:transform .25s cubic-bezier(.3,.7,.2,1)}
.pseg button{position:relative;z-index:1;border:0;background:none;display:flex;flex-direction:column;align-items:center;gap:1px;padding:8px 4px 7px;border-radius:12px;cursor:pointer;color:var(--muted);min-width:0}
.pseg b{font-size:14px;font-weight:700;color:var(--ink);opacity:.7}
.pseg small{font-size:11px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.pseg button:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.whead{display:flex;align-items:center;gap:10px;padding-right:40px}
.wico{flex:none;width:40px;height:40px;border-radius:14px;display:grid;place-items:center;font-size:20px;background:linear-gradient(135deg,var(--accent-soft),var(--bg))}
.whead h2{margin:0;font-size:17px}.whead small{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)}

.rhst{font-size:11.5px;font-weight:800;padding:4px 10px;border-radius:99px}.rhst.ok{color:var(--ok);background:color-mix(in srgb,var(--ok) 13%,transparent)}.rhst.mid{color:color-mix(in srgb,var(--s3) 75%,var(--ink));background:color-mix(in srgb,var(--s3) 20%,transparent)}.rhst.bad{color:var(--bad);background:color-mix(in srgb,var(--bad) 13%,transparent)}
.rhst.na{color:var(--muted);background:var(--bg)}
.kpbtns{margin-left:auto;display:inline-flex;gap:6px;align-self:center;flex-wrap:wrap;justify-content:flex-end}.kpbtns .aiq1{margin-left:0}
.cwq.same{background:var(--bg);color:var(--muted)}.cwq.chg{background:color-mix(in srgb,var(--s3) 22%,transparent);color:color-mix(in srgb,var(--s3) 70%,var(--ink))}.cwq[disabled]{opacity:.7}
.cwtool{display:flex;align-items:center;gap:10px;margin-top:12px}
.cwseg{width:150px;flex:none;background:var(--bg);box-shadow:none;padding:3px;border-radius:12px}.cwseg .pind{top:3px;bottom:3px;left:3px;width:calc((100% - 6px)/3);border-radius:9px;background:var(--card);box-shadow:0 1px 3px rgba(20,20,60,.12)}
.cwseg button{padding:6px 4px;font-size:13px;font-weight:700;border-radius:9px}.cwseg button[aria-checked="true"]{color:var(--ink)}
.cwrange{font-size:12px;color:var(--muted);font-weight:600}
.cwhero{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:12px}
.cwhero>div{display:flex;flex-direction:column;align-items:center;padding:9px 2px 7px;border-radius:12px;background:var(--bg)}
.cwhero b{font-size:21px;font-family:var(--f-display);line-height:1.1}.cwhero small{font-size:10.5px;font-weight:700;color:var(--muted);white-space:nowrap}
.cwhero .mid b{color:color-mix(in srgb,var(--s3) 75%,var(--ink))}.cwhero .ok b{color:var(--ok)}.cwhero .bad b{color:var(--bad)}.cwhero .na b{color:var(--muted)}.cwhero .zero{opacity:.45}
.cwgh{display:flex;align-items:center;justify-content:space-between;margin:14px 2px 6px}.cwgh b{font-size:12.5px;color:var(--muted)}.cwgh .num{color:var(--ink);margin-left:2px}.cwgh .linkbtn{text-decoration:none;color:var(--accent);font-weight:700;padding:2px 4px;min-height:0;font-size:12px}
.cwgrp{display:flex;flex-direction:column;gap:6px}
.cwi{position:relative;display:grid;grid-template-columns:auto 44px 1fr;gap:10px;align-items:start;padding:10px 12px 10px 10px;border-radius:14px;background:var(--bg);box-shadow:inset 0 0 0 1.5px transparent;cursor:pointer;transition:box-shadow .15s,background .15s}
.cwi.on{background:var(--card);box-shadow:inset 0 0 0 1.5px var(--accent),0 2px 10px rgba(20,20,60,.06)}
.cwi.off{grid-template-columns:44px 1fr;cursor:default}
.cwck{position:absolute;opacity:0;pointer-events:none}.cwtick{width:20px;height:20px;margin-top:8px;border-radius:50%;box-shadow:inset 0 0 0 2px var(--line);background:var(--card);display:grid;place-items:center;transition:all .15s}
.cwi.on .cwtick{background:var(--accent);box-shadow:none}.cwi.on .cwtick:after{content:"";width:5px;height:9px;border:solid #fff;border-width:0 2px 2px 0;transform:translateY(-1px) rotate(45deg)}
.cwck:focus-visible+.cwtick{outline:2px solid var(--accent);outline-offset:2px}
.cwdate{display:flex;flex-direction:column;align-items:center;padding:4px 0;border-radius:10px;background:var(--card)}.cwi.on .cwdate{background:var(--accent-soft)}
.cwdate b{font-size:19px;font-family:var(--f-display);line-height:1.1}.cwdate small{font-size:10px;font-weight:700;color:var(--muted);white-space:nowrap}
.cwbody{min-width:0;display:flex;flex-direction:column;gap:5px}
.cwtop{display:flex;align-items:flex-start;gap:6px}.cwtop b{flex:1;min-width:0;font-size:13.5px;line-height:1.35}.cwtop .rhst{flex:none;font-size:10.5px;padding:3px 8px;white-space:nowrap}
.cwbar{display:flex;gap:1.5px;height:8px;border-radius:99px;overflow:hidden}.cwbar i{display:block;min-width:2px}
.cwsub{font-size:11.5px;color:var(--muted);line-height:1.45}.cwsub b{color:var(--ink);font-weight:700}
.cwfrom{font-size:11.5px;color:var(--muted);line-height:1.4}.cwfrom s{opacity:.75}
.cwdone{margin-top:12px;padding:12px;border-radius:12px;text-align:center;font-size:13px;font-weight:700;color:var(--ok);background:color-mix(in srgb,var(--ok) 10%,transparent)}
.cwfoot{position:sticky;bottom:calc(72px + env(safe-area-inset-bottom,0px));z-index:2;display:flex;align-items:center;gap:10px;margin:14px -4px 0;padding:10px 10px 10px 14px;border-radius:16px;background:var(--card);box-shadow:0 4px 18px rgba(20,20,60,.14)}
.cwfoot .btn{flex:none;margin:0}.cwfmsg{flex:1;min-width:0;font-size:12.5px;color:var(--muted);line-height:1.4}.cwfmsg b{color:var(--ink)}
.fithow{margin-top:14px;border-top:1px solid var(--line);padding-top:10px}
.fithow>summary{list-style:none;display:flex;align-items:center;justify-content:space-between;cursor:pointer;font-size:13.5px;font-weight:700}.fithow>summary::-webkit-details-marker{display:none}
.fithow[open]>summary .chev:before{transform:translateY(2px) rotate(-135deg)}
.fithow ol{margin:10px 0 0;padding:0;list-style:none;counter-reset:fh;display:flex;flex-direction:column;gap:8px}
.fithow li{counter-increment:fh;display:grid;grid-template-columns:24px 1fr;gap:8px;font-size:12.5px;line-height:1.55;color:var(--muted)}.fithow li b{color:var(--ink)}
.fithow li:before{content:counter(fh);width:22px;height:22px;border-radius:50%;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent);font-weight:800;font-size:12px}
.fithow p{margin:10px 0 0;font-size:12px;color:var(--muted)}
.gmhero{grid-template-columns:repeat(3,1fr)}
.fitnext{margin-top:12px;padding:12px 14px;border-radius:14px;background:color-mix(in srgb,var(--ok) 10%,var(--card));box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--ok) 45%,transparent);animation:fdin .2s ease-out}
.fitnh{display:flex;align-items:center;justify-content:space-between;gap:8px}.fitnh b{font-size:13.5px;color:var(--ok)}
.fitnx{border:0;background:none;color:var(--muted);font-size:14px;width:28px;height:28px;border-radius:50%;cursor:pointer}
.fitnext ol{margin:8px 0 0;padding:0;list-style:none;counter-reset:fn;display:flex;flex-direction:column;gap:5px}
.fitnext li{counter-increment:fn;display:grid;grid-template-columns:20px 1fr;gap:6px;font-size:12.5px;line-height:1.5;color:var(--ink)}
.fitnext li:before{content:counter(fn);width:18px;height:18px;margin-top:1px;border-radius:50%;display:grid;place-items:center;background:var(--ok);color:#fff;font-weight:800;font-size:11px}
```

## 5. 화면에 연결하기

### 5-1. 오늘 탭 (`vToday`)
훈련 카드에서 `data-aiq` '💬 코치 팁' 버튼을 만드는 부분을 찾아. 보통 `if(!folded&&t[0]!=="r"){var kz=...` 블록이야.
- 가민 버튼 HTML: `var gb=S.watch==="freddy"?fitDayBtn(sel,t):"";`
- 키 페이스 줄(`.keypace`)이 있으면, 코치 팁 버튼과 `gb`를 `<span class="kpbtns">…</span>`로 함께 감싸서 오른쪽에 나란히 놓아.
- 키 페이스 줄이 없는 날은 `if(gb)h+='<div class="keypace"><span class="kpbtns">'+gb+'</span></div>';`
- 그 바로 뒤에 `if(FITNEXT===sel&&S.watch==="freddy")h+=fitNextHtml(1);`
- 이벤트는 `[data-aiq]` 버튼을 바인딩하는 곳 바로 아래에 넣어.
```js
view.querySelectorAll("[data-fitdl]").forEach(function(b){b.onclick=function(){fitDownload(b.dataset.fitdl)}});
view.querySelectorAll("[data-fitnx]").forEach(function(b){b.onclick=function(){FITNEXT="";GM.done=0;keepIf()}});
```
- COROS 연결일 때 보이던 기존 버튼(있다면 '⌚ 워치로 보내기')은 그대로 둬. 가민일 때만 가민 버튼이 나오게 해.

### 5-2. 훈련표 탭 툴바 (`planStrip` / `bindPlanStrip`)
- 열림 상태 변수 `var GMOPEN=false;`를 추가해. `planStrip`의 `open` 계산에 `||(isGarmin()&&GMOPEN)`를 더해.
- '📈 전체 진행' 줄 바로 앞에, `isGarmin()`일 때만 아래 줄을 넣어.
```js
if(isGarmin()){var gmn=0,gm=S.gfit||{};cwDates(28).forEach(function(ds){var r=gm[ds];if(!r)return;var c=cwCourse(cwPlanOf(ds),ds);if(!c||cwHash(c.course)!==r.h)gmn++});
  h+='<div class="srow wrow"><span class="sl" aria-hidden="true">⌚</span><button class="wlink" data-ptog="pgm" aria-expanded="'+GMOPEN+'"><b>가민 워크아웃 파일'+(gmn?' · <span style="color:var(--accent)">계획 변경 '+gmn+'일</span>':Object.keys(gm).length?' · <span class="muted">받은 그대로</span>':' · <span class="muted">아직 안 받음</span>')+'</b><span>.fit 파일 zip으로 받아 USB로 워치에 · 1~4주</span></button><button class="sbtn wset'+(GMOPEN?' on':'')+'" data-ptog="pgm" aria-expanded="'+GMOPEN+'">'+(GMOPEN?'닫기':'받기')+'</button></div>'}
```
- 패널 출력: 다른 패널(`pwrap("psrc",…)` 등)을 붙이는 곳에 `if(isGarmin()&&GMOPEN)h+=pwrap("pgm",gmCard());`를 추가해.
- 토글: `[data-ptog]` 클릭 처리에서 `k==="pgm"`이면 `GMOPEN=!GMOPEN`이 되게 해. 요약 줄을 접을 때(`[data-pstrip]`)는 `GMOPEN=false`로 해.
- `bindPlanStrip()` 끝에서 `bindGm();`을 호출해.
- 닫기 ✕: `bindPclose()`의 분기에 `else if(k==="pgm")GMOPEN=false;`를 추가해.
- 이미 'COROS 워크아웃 맞추기'(`data-ptog="pcw"`)가 있는 버전이면, 새 줄을 만들지 않아도 돼. 그 줄과 패널이 `isGarmin()`일 때 가민용으로 바뀌게 해.
  - `cwCard()` 맨 앞에 `if(isGarmin())return gmCard();`
  - `bindCw()` 맨 앞에 `if(isGarmin()){bindGm();return}`
  - 줄 글자도 위 가민 문구로 바꿔.

### 5-3. 가이드 (`vGuide`)
'계획 수립 · 일정 조정' 카드 바로 뒤에 아래 카드를 이어 붙여. 가이드 HTML 문자열 안이라 `'+ … +'` 연결 형태 그대로야.
```js
'+'<div class="card"><h2>가민 워크아웃 파일</h2><p class="small muted" style="margin:2px 0 6px">워치 연결이 가민·애플(freddy)일 때 · 가민은 앱에서 바로 보낼 수 없어서 파일로 받아 USB로 넣어요</p><ul class="tight small gl"><li><b>하루씩:</b> 오늘 탭 훈련 카드의 <b>⌚ 가민 파일 받기</b></li><li><b>한꺼번에:</b> 훈련표 맨 위 요약 줄 → ⌚ 가민 워크아웃 파일 <b>받기</b> → 기간(1·2·4주)과 날짜 고르고 <b>파일 받기</b> → .fit 여러 개가 zip 하나로</li><li><b>버튼 표시:</b> ⌚ 가민 파일 받기(안 받음) · ⌚ 바뀜 · 다시 받기(받은 뒤 계획 변경) · ⌚ 받음 ✓(그대로)</li><li><b>워치 이름:</b> 날짜+영문 요약(예: 1117 E 8K Strides x4). 페이스는 구간별 범위로 들어가요</li></ul>'+fitHowHtml(true)+'</div>'+'
```
- 가이드를 그룹으로 재배치하는 목록(예: `["훈련표",["계획 수립 · 일정 조정", …]]`)이 있으면, '계획 수립 · 일정 조정' 다음에 `"가민 워크아웃 파일"`을 넣어.
- 가이드 카드를 자동으로 접는 코드가 있으면 이 카드도 똑같이 접혀도 돼.
- 'COROS 워크아웃 보내기' 카드가 있으면, 제목 아래에 `워치 연결이 COROS일 때` 한 줄을 넣고 가민 관련 문장은 그 카드에서 빼.

## 6. 확인하고 알려줘
1. 문법 오류 없이 페이지가 열리는지
2. 워치 연결을 '가민·애플'로 바꿨을 때
   - 오늘 탭 훈련 카드에 '⌚ 가민 파일 받기'가 보이는지
   - 훈련표 요약 줄에 '⌚ 가민 워크아웃 파일'이 보이는지
3. 워치 연결이 COROS일 때 기존 화면이 바뀌지 않았는지
4. 파일 받기를 누르면 저장 확인 창이 뜨는지. 파일 이름은 `sub3-YYYYMMDD-garmin.zip` 또는 `sub3-garmin-MMDD-MMDD.zip`이야.
5. 저장 후 초록색 '워치에 넣기' 4단계 상자가 뜨는지
6. 가이드 훈련표 그룹에 '가민 워크아웃 파일' 카드가 있는지
7. 게시 후 저장된 권한 선언이 이전과 같은지. 바꿨다면 무엇을 바꿨는지 한 줄로.

## 참고
- .fit 파일은 FIT 프로토콜 workout 파일이야(file_id + workout + workout_step). 페이스 범위는 speed 목표(m/s×1000)로, 반복은 `repeat_until_steps_cmplt`로 들어가. 가민 공식 FIT SDK로 읽어서 정상 파일임을 확인한 코드야.
- 워치 이름은 한글 깨짐을 막으려고 날짜+영문 요약으로 만들어(예: `1117 E 8K Strides x4`).
- 가민 커넥트(앱·웹)에는 워크아웃 파일 가져오기가 없어서, USB로 워치의 `GARMIN/NewFiles` 폴더에 복사하는 방법으로 안내해.
