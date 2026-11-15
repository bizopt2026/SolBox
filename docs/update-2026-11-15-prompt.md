# 작업 지시: 11/15 업데이트 (인스타 스토리 하루 합산 + 대회 주 카드)

> 아래 전체를 복사해서, **복제한 'Sub-3 훈련 체크' 앱을 만든 Claude 대화**에 붙여 넣으세요.
> 바로 아래 `<여기에 내 앱 링크>` 자리에 내 앱(복제본) 링크를 넣어 주세요.

---

내 아티팩트 "Sub-3 훈련 체크"에 아래 두 가지 업데이트를 적용해서 **같은 URL에 다시 게시**해 줘.

- 아티팩트: <여기에 내 앱 링크>

## 0. 먼저 지킬 것
1. `artifact-capabilities` 스킬과 `artifact-design` 스킬을 먼저 로드해.
2. 아티팩트를 `read`로 읽고, **그 원본 파일을 고쳐서** 같은 `url`로 재게시해. 새 아티팩트를 만들지 마.
3. 게시할 때 `capabilities`와 `contract`를 넘기지 마. 기존 권한 선언을 그대로 이어가.
4. 아래에 적은 함수만 **통째로 교체**하고, 다른 기능(COROS·가민·식단·AI 코치 등)은 건드리지 마.
5. 교체할 함수가 원래 앱에 없거나 이름이 다르면, 같은 역할을 하는 함수를 찾아 교체하고 무엇을 바꿨는지 알려줘.
6. 앱 버전 값(`APP_VERSION`, `APP_RELEASED`, `VLOG`)이 있으면 **바꾸지 마.** 복제한 앱의 버전은 복제 시점 값으로 고정해서 관리해.

## 1. 바뀌는 내용
### A. 인스타 스토리: 하루에 여러 번 끊긴 훈련을 하루 전체로
워치를 멈췄다 다시 시작해서 하루 기록이 2개 이상일 때의 처리야.
- **랩표**
  - 모든 회차의 랩을 시간순으로 이어서 보여줘.
  - 랩이 없는 회차는 **'전체'** 한 줄(거리·시간·페이스·심박)로 넣어.
  - 랩 번호는 회차마다 1부터 다시 매기고, 회차가 시작하는 줄에 주황색 **'1회차'·'2회차'** 표시와 점선 구분선을 넣어.
  - 제목은 상황에 따라 '랩 N개 · 2회 합산', '구간 N개 · 2회 합산', '랩 3개 · 2회차'로 보여줘.
- **그래프**
  - 모든 회차의 초 단위 기록을 이어 붙여 거리축 하나에 그려.
  - 회차 경계에 주황 점선과 '2회차' 표시를 넣고, 페이스 선은 경계에서 끊어.
  - **랩 평균선은 그 랩이 속한 회차 구간 안에만** 그려. 회차 번호는 하루 전체 기록 순서야. 랩이 있는 기록끼리만 세면 안 돼.
  - 초 단위 기록은 그날 COROS 기록 **모두**를 차례로 받아.
- **AI 멘트**
  - 하루 합계와 회차별 요약(시각·거리·페이스·심박·본 구간)을 넘겨서 **하루 전체를 하나로 평가**하게 해.
  - 그날 기록 개수가 바뀌면 멘트를 새로 써.
  - 회차 '전체' 줄은 본 구간(강조)에서 빼.

### B. 대회 주 카드 (오늘 탭, 대회 7일 전부터)
- **기본은 접힘**: 남색→보라 그라데이션 띠 하나에 아래가 모두 보여.
  - D-day 배지
  - 날짜·목표 기록(있을 때만)·대회 이름
  - '다음 · 할 일'
  - 준비 진행 링(n/5)
- **누르면 펼침**: 준비 항목 5개가 아이콘과 시점 태그(D-2~D-1 등)가 붙은 카드로 나와. 카드를 누르면 체크(초록 ✓, 취소선)돼.
  - 맨 아래에 '🏁 레이스 전략 보기 →' 버튼(페이스 탭 레이스 전략으로 이동)을 둬.
- **접힘 상태 기억**: 기존 `foldOpen("rw",false)` / `foldSet("rw",…)`를 써.
- **대회 이름**: 대회 목록에 없는 대회는 훈련표 그날 첫 줄(예: '인천마라톤 풀코스')로 보여줘.

## 2. 앱에 이미 있어야 하는 것 (확인만)
`S`, `save()`, `keepIf()`, `render()`, `view`, `esc()`, `T`, `RACE`, `TOTAL`, `K`, `W`, `planAt()`, `idxOf()`, `addDays()`, `md()`, `p()`, `DOW`, `ptxt()`, `wn()`, `P()`, `fmtT()`, `fmtMS()`, `hhmm()`, `dayRec()`, `runsOn()`, `fetchSeries()`, `foldOpen()`, `foldSet()`, `rrect()`, `enterSub()`, `wxLoad()`, `drawStory()`, `STORY`, `SER`, `WX`

## 3. 교체할 함수 — 인스타 스토리
아래 6개 함수를 원래 앱의 같은 이름 함수와 **통째로 교체**해.
`storyData`, `openStory`, `storyComment`, `drawSeriesChart`, `lapRows`, `drawLapPanel`
```js
function storyData(ds){
  var i=idxOf(ds),w=Math.floor(i/7),t=planAt(i),r=dayRec(ds),km=null,dur=null,hr=null;
  if(r.w){km=r.w.km;dur=r.w.dur;hr=r.w.hr}if(r.km&&!isNaN(+r.km))km=+r.km;if(r.hr&&!isNaN(+r.hr))hr=+r.hr;
  // a day can hold several runs (watch stopped and restarted): join their laps and per-second series in time order
  var rs=runsOn(ds),lr=rs.filter(function(x){return x.laps&&x.laps.laps&&x.laps.laps.length}),all=null,laps=null,whole=0;
  if(lr.length||rs.length>1){var lp=[];rs.forEach(function(x,ri){if(x.laps&&x.laps.laps&&x.laps.laps.length)x.laps.laps.forEach(function(q){var c={};for(var k in q)c[k]=q[k];c.seg=ri;lp.push(c)});
      else if(rs.length>1&&x.km&&x.dur){whole++;lp.push({d:x.km,t:x.dur,p:x.dur/x.km,hr:x.hr||null,seg:ri,whole:true})}});
    var allMan=lr.every(function(x){return x.laps.kind==="manual"});all=!lp.length?null:whole||!lr.length?{kind:"mixed",laps:lp,parts:rs.length,only:null}:{kind:allMan?"manual":lr.length>1?"mixed":lr[0].laps.kind,laps:lp,parts:lr.length,only:lr.length===1&&rs.length>1?rs.indexOf(lr[0])+1:null}}
  if(all&&all.kind==="manual")laps=all;
  var work=all?all.laps.filter(function(x){return !x.whole&&x.d>=0.4&&x.p<330*K}):[];
  var cr=rs.filter(function(x){return x.src==="COROS"}),miss=cr.filter(function(x){return !x.series}),ser=null;
  if(cr.length&&!miss.length){var d=[],pc=[],h=[],off=0,cuts=[],spans={};cr.forEach(function(x,ri){var S0=x.series,len=S0.d[S0.d.length-1]||0;if(ri){cuts.push(off);d.push(off);pc.push(0);h.push(0)}spans[rs.indexOf(x)]=[off,off+len];S0.d.forEach(function(v,j){d.push(off+v);pc.push(S0.p[j]);h.push(S0.h[j])});off+=len});ser={d:d,p:pc,h:h,parts:cr.length,cuts:cuts,spans:spans}}
  else if(!cr.length)rs.forEach(function(x){if(!ser&&x.series)ser=x.series});
  var sess=rs.map(function(x){var wk=x.laps&&x.laps.laps?x.laps.laps.filter(function(q){return q.d>=0.4&&q.p<330*K}):[];return{km:x.km,dur:x.dur,hr:x.hr,pace:x.km&&x.dur?x.dur/x.km:null,work:wk,at:x.ts?hhmm(x.ts):""}});
  return{ds:ds,i:i,w:w,t:t,km:km,dur:dur,hr:hr,pace:km&&dur?dur/km:null,work:work,rpe:r.rpe,note:r.note,laps:all,series:ser,serRun:miss[0]||null,serMiss:miss,parts:rs.length,sess:sess};
}
function openStory(ds){var D0=storyData(ds);if(D0.serMiss&&D0.serMiss.length){STORY.serBusy=true;D0.serMiss.reduce(function(pr,x){return pr.then(function(){return fetchSeries(x)})},Promise.resolve()).then(function(){STORY.serBusy=false;if(tab==="story"){render()}})}
if(STORY.ds!==ds||STORY.parts!==D0.parts){STORY.comment="";STORY.out="";STORY.msg="";STORY.err=""}STORY.ds=ds;STORY.parts=D0.parts;enterSub("story");render();window.scrollTo(0,0);if(!STORY.comment)storyComment();if(ds>=addDays(T,-1)&&S.wx&&S.wx.key&&!WX.busy)wxLoad(false).then(function(){if(tab==="story")drawStory()})}
async function storyComment(){
  var D=storyData(STORY.ds);STORY.busy=true;STORY.err="";if(tab==="story")render();
  try{
    var sm=window.claude&&window.claude.use?await window.claude.use("sample"):null;
    if(!sm){STORY.err="claude.ai에 로그인해야 AI 코멘트를 쓸 수 있어요. 직접 적어도 돼요.";return}
    var info="날짜 "+D.ds+" ("+wn(D.w)+" "+W[D.w].lab+"), 계획: "+ptxt(D.t[1]).replace(/\n/g," / ")+"\n실제: "+(D.km?D.km+"km":"거리 기록 없음")+(D.dur?" · "+fmtT(D.dur):"")+(D.pace?" · 평균 "+fmtMS(D.pace)+"/km":"")+(D.hr?" · 평균 심박 "+D.hr:"")+
      (D.parts>1?"\n※ 이 날은 워치를 멈췄다 다시 시작해 기록이 "+D.parts+"개로 나뉘었지만 하나의 훈련이야. 위 '실제'는 하루 합계야.\n회차별: "+D.sess.map(function(q,k){return (k+1)+"회차"+(q.at?"("+q.at+")":"")+" "+(q.km?q.km+"km":"")+(q.pace?" "+fmtMS(q.pace)+"/km":"")+(q.hr?" 심박"+q.hr:"")+(q.work.length?" · 본 구간 "+q.work.map(function(x){return x.d.toFixed(1)+"km "+fmtMS(x.p)}).join(", "):"")}).join(" / "):"")+
      (D.work.length?"\n하루 전체 본 구간 랩: "+D.work.map(function(x){return x.d.toFixed(1)+"km "+fmtMS(x.p)+(x.hr?" 심박"+x.hr:"")}).join(", "):"")+
      "\n목표: "+(S.goal?fmtT(S.goal.sec):"2:59:59")+" (MP "+P(255)+"/km), 2027-03-21 서울마라톤까지 D-"+Math.round((p(RACE)-p(D.ds))/864e5);
    var out=await sm("너는 이 러너의 마라톤 코치야. 오늘 훈련을 인스타 스토리에 올릴 짧은 코치 코멘트를 한국어로 써.\n- 2~3문장, 공백 포함 90자 이내.\n- 하루 훈련 전체(모든 회차 합계)를 하나로 평가해. 특정 회차만 오늘 훈련인 것처럼 쓰지 마.\n- 아래 숫자 중 1~2개를 근거로 잘한 점과 다음 포인트를 하나씩.\n- 친근하고 자신감 있게. 해시태그·이모지는 쓰지 마. 따옴표 없이 본문만.\n\n"+info,{cache:false,modelTier:"default"});
    STORY.comment=String(out&&out.text||"").replace(/\*\*/g,"").replace(/^\s*(?:[-*•]|\d+[.)])\s+/gm,"").replace(/\s*\n\s*/g," ").replace(/^["'“]|["'”]$/g,"").trim().slice(0,160);
  }catch(e){var c=e&&e.code;STORY.err=c==="not_granted"?"이 앱의 클로드 사용을 허용해야 AI 코멘트를 쓸 수 있어요. 직접 적어도 돼요.":c==="rate_limited"?"요청이 많아요. 잠시 후 다시 시도해 주세요.":"코멘트를 만들지 못했어요. 직접 적어도 돼요."}
  finally{STORY.busy=false;if(tab==="story"){render();drawStory()}}
}
function drawSeriesChart(ctx,D,L,x,y,w,h,FD,FB){
  var Sr=D.series,cx=x+118,cw=w-118-44,top=y+104,bot=y+h-96,ch=bot-top,dmax=Sr.d[Sr.d.length-1]||1;
  var lapsA=L&&L.laps?L.laps:[],vals=Sr.p.filter(function(v){return v>0}).slice().sort(function(a,b){return a-b});if(!vals.length)return;
  var pmin=Math.min(vals[Math.floor(vals.length*0.02)],lapsA.length?Math.min.apply(null,lapsA.map(function(l){return l.p})):9999),pmax=vals[Math.floor(vals.length*0.95)];
  pmax=Math.min(pmax,pmin+300);var step=(pmax-pmin)>200?60:30,lo=Math.floor((pmin-8)/step)*step,hi=pmax+15;
  function X(dm){return cx+Math.min(1,dm/dmax)*cw}function Y(pc){return top+(Math.min(Math.max(pc,lo),hi)-lo)/(hi-lo)*ch}
  // heart rate: soft shading behind
  var hs=Sr.h.filter(Boolean),hmin=hs.length?Math.min.apply(null,hs):0,hmax=hs.length?Math.max.apply(null,hs):0;
  if(hs.length>5){var hg=ctx.createLinearGradient(0,top,0,bot);hg.addColorStop(0,"rgba(122,167,255,.32)");hg.addColorStop(1,"rgba(122,167,255,.03)");ctx.fillStyle=hg;ctx.beginPath();ctx.moveTo(X(Sr.d[0]),bot);
    Sr.h.forEach(function(v,i){if(v)ctx.lineTo(X(Sr.d[i]),bot-ch*(0.08+0.72*(v-hmin)/((hmax-hmin)||1)))});ctx.lineTo(X(dmax),bot);ctx.closePath();ctx.fill()}
  // grid
  ctx.font="500 24px "+FD;ctx.textAlign="right";for(var tk=Math.ceil(lo/step)*step;tk<=hi;tk+=step){var gy=Y(tk);ctx.strokeStyle="rgba(255,255,255,.12)";ctx.lineWidth=2;ctx.setLineDash([6,8]);ctx.beginPath();ctx.moveTo(cx,gy);ctx.lineTo(cx+cw,gy);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle="rgba(255,255,255,.5)";ctx.fillText(fmtMS(tk),cx-14,gy+8)}ctx.textAlign="left";
  // lap boundaries by distance + lap average lines
  // each lap is drawn inside the span of the run it belongs to (scaled to that run's watch distance)
  var SP=Sr.spans,tot={};lapsA.forEach(function(l){var g=l.seg||0;tot[g]=(tot[g]||0)+l.d*1000});
  var cumG={},segs=lapsA.map(function(l){var g=l.seg||0,sp=SP&&SP[g],st=sp?sp[0]:0,span=sp?sp[1]-sp[0]:dmax,f=sp&&tot[g]?span/tot[g]:1,a=st+(cumG[g]||0)*f;cumG[g]=(cumG[g]||0)+l.d*1000;return{l:l,a:a,b:st+cumG[g]*f}}).filter(function(q){return !SP||SP[q.l.seg||0]});
  segs.forEach(function(q,k){if(k){ctx.strokeStyle="rgba(255,255,255,.10)";ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(X(q.a),top);ctx.lineTo(X(q.a),bot);ctx.stroke()}});
  // run boundaries when the watch was stopped and restarted
  (Sr.cuts||[]).forEach(function(cd,k){var bx=X(cd);ctx.strokeStyle="rgba(255,154,92,.85)";ctx.lineWidth=3;ctx.setLineDash([10,8]);ctx.beginPath();ctx.moveTo(bx,top-6);ctx.lineTo(bx,bot);ctx.stroke();ctx.setLineDash([]);
    ctx.font="700 22px "+FB;var lb=(k+2)+"회차",tw=ctx.measureText(lb).width+16;ctx.fillStyle="rgba(255,154,92,.9)";rrect(ctx,Math.min(bx-tw/2,cx+cw-tw),top-40,tw,30,8);ctx.fill();ctx.fillStyle="#1a1206";ctx.textAlign="center";ctx.fillText(lb,Math.min(bx,cx+cw-tw/2),top-18);ctx.textAlign="left"});
  // per-second pace line
  ctx.strokeStyle="rgba(255,255,255,.92)";ctx.lineWidth=3.5;ctx.lineJoin="round";ctx.beginPath();var pen=false;
  Sr.p.forEach(function(v,i){if(!v){pen=false;return}var px=X(Sr.d[i]),py=Y(v);if(pen)ctx.lineTo(px,py);else{ctx.moveTo(px,py);pen=true}});ctx.stroke();
  // lap averages: straight line + value
  var lastX=-999;segs.forEach(function(q){var hard=!q.l.whole&&q.l.d>=0.4&&q.l.p<330*K,x0=X(q.a),x1=X(q.b),yy=Y(q.l.p);if(x1-x0<10)return;
    ctx.strokeStyle=hard?"#9d94ff":"rgba(200,195,255,.7)";ctx.lineWidth=hard?5:3;ctx.setLineDash(hard?[]:[10,8]);ctx.beginPath();ctx.moveTo(x0+3,yy);ctx.lineTo(x1-3,yy);ctx.stroke();ctx.setLineDash([]);
    var mid=(x0+x1)/2;if((!hard&&x1-x0<70)||mid-lastX<80)return;lastX=mid;
    var txt=fmtMS(q.l.p);ctx.font=(hard?"800 30px ":"600 24px ")+FD;var tw=ctx.measureText(txt).width+18,ly=Math.max(top+4,yy-46);
    ctx.fillStyle=hard?"#9d94ff":"rgba(0,0,0,.45)";rrect(ctx,Math.min(Math.max(mid-tw/2,cx),cx+cw-tw),ly,tw,hard?40:34,10);ctx.fill();
    ctx.fillStyle="#fff";ctx.textAlign="center";ctx.fillText(txt,Math.min(Math.max(mid,cx+tw/2),cx+cw-tw/2),ly+(hard?30:25));ctx.textAlign="left"});
  // axes + legend
  ctx.fillStyle="rgba(255,255,255,.55)";ctx.font="500 24px "+FB;ctx.fillText("0km",cx,bot+32);ctx.textAlign="right";ctx.fillText((Math.round(dmax/100)/10)+"km",cx+cw,bot+32);ctx.textAlign="left";
  ctx.fillStyle="rgba(255,255,255,.9)";ctx.fillRect(cx,y+h-30,30,5);ctx.fillText("페이스(초 단위)",cx+40,y+h-20);
  ctx.fillStyle="#ff9a5c";var lx=cx+40+ctx.measureText("페이스(초 단위)").width+28;ctx.fillRect(lx,y+h-30,30,5);ctx.fillText("랩 평균",lx+40,y+h-20);
  if(hs.length>5){ctx.textAlign="right";ctx.fillStyle="#9cc0ff";ctx.fillText("심박 "+hmin+"~"+hmax,cx+cw,y+h-20);var tw2=ctx.measureText("심박 "+hmin+"~"+hmax).width;ctx.fillStyle="rgba(122,167,255,.45)";ctx.fillRect(cx+cw-tw2-44,y+h-36,32,18);ctx.textAlign="left"}
}
function lapRows(L,n){var cn={},a=L.laps.map(function(x,i){x.i=i+1;var g=x.seg||0;cn[g]=(cn[g]||0)+(x.whole?0:1);x.n=cn[g];return x});if(a.length<=n)return a;
  var work=a.filter(function(x){return x.whole||(x.d>=0.4&&x.p<330*K)}).slice(0,n),rest=a.filter(function(x){return work.indexOf(x)<0}).sort(function(p,q){return q.d-p.d}).slice(0,n-work.length);
  return work.concat(rest).sort(function(p,q){return p.i-q.i})}
function drawLapPanel(ctx,D,x,y,w,yb,FD,FB){
  var L=D.laps,h=yb-y;if(h<220)return;
  if(STORY.view==="table"){var nr=lapRows(L,Math.max(2,Math.floor((h-150)/52))).length;h=Math.min(h,150+nr*52);y=yb-h}
  ctx.fillStyle="rgba(8,12,24,.55)";rrect(ctx,x,y,w,h,32);ctx.fill();
  ctx.fillStyle="#9d94ff";ctx.font="800 30px "+FB;ctx.fillText(L?(L.kind==="manual"?"랩 "+L.laps.length+"개":L.kind==="mixed"?"구간 "+L.laps.length+"개":"1km 구간")+(L.parts>1?" · "+L.parts+"회 합산":L.only?" · "+L.only+"회차":""):"페이스 흐름",x+36,y+56);
  ctx.textAlign="right";ctx.fillStyle="rgba(255,255,255,.6)";ctx.font="500 26px "+FB;ctx.fillText(STORY.view==="table"?"랩 기록":(D.series?"초 단위 · 랩 평균":"랩별 평균 페이스"),x+w-36,y+56);ctx.textAlign="left";
  if(STORY.view==="table"){
    var rows=lapRows(L,Math.max(2,Math.floor((h-150)/52))),cols=[["#",x+36,"left"],["거리",x+150,"left"],["시간",x+w*0.42,"left"],["페이스",x+w*0.64,"left"],["심박",x+w-36,"right"]],ty=y+108;
    ctx.font="500 26px "+FB;ctx.fillStyle="rgba(255,255,255,.55)";cols.forEach(function(c){ctx.textAlign=c[2];ctx.fillText(c[0],c[1],ty)});
    rows.forEach(function(r,k){var yy=ty+52*(k+1),hard=!r.whole&&r.d>=0.4&&r.p<330*K;var newSeg=r.seg!=null&&(L.parts>1||L.only)&&(k===0||rows[k-1].seg!==r.seg);if(newSeg){if(k){ctx.strokeStyle="rgba(255,154,92,.8)";ctx.lineWidth=2;ctx.setLineDash([8,6]);ctx.beginPath();ctx.moveTo(x+16,yy-40);ctx.lineTo(x+w-16,yy-40);ctx.stroke();ctx.setLineDash([])}ctx.font="700 18px "+FB;var lb2=(r.seg+1)+"회차",tw3=ctx.measureText(lb2).width+16,pcx=x+w*0.31;ctx.fillStyle="rgba(255,154,92,.95)";rrect(ctx,pcx-tw3/2,yy-51,tw3,22,7);ctx.fill();ctx.fillStyle="#1a1206";ctx.textAlign="center";ctx.fillText(lb2,pcx,yy-34);ctx.textAlign="left"}if(hard){ctx.fillStyle="rgba(157,148,255,.18)";ctx.fillRect(x+16,yy-36,w-32,48)}
      ctx.fillStyle=hard?"#fff":"rgba(255,255,255,.75)";ctx.font=(hard?"800 ":"600 ")+"34px "+FD;
      [[r.whole?"전체":String(L.parts>1||L.only?r.n:r.i),0],[r.d.toFixed(2),1],[fmtT(r.t),2],[fmtMS(r.p),3],[r.hr?String(r.hr):"-",4]].forEach(function(v){var c=cols[v[1]];ctx.textAlign=c[2];if(v[1]===3)ctx.fillStyle=hard?"#ff9a5c":"rgba(255,255,255,.75)";else ctx.fillStyle=hard?"#fff":"rgba(255,255,255,.75)";ctx.fillText(v[0],c[1],yy)})});
    ctx.textAlign="left";if(rows.length<L.laps.length){ctx.fillStyle="rgba(255,255,255,.5)";ctx.font="500 24px "+FB;ctx.textAlign="right";ctx.font="500 22px "+FB;ctx.fillText("전체 "+L.laps.length+"개 중 "+rows.length+"개",x+w-36,y+h-12);ctx.textAlign="left"}
    return;
  }
  if(D.series){drawSeriesChart(ctx,D,L,x,y,w,h,FD,FB);return}
  // pace chart: each lap's average pace as a flat line across its distance (faster = higher), labelled; HR as soft shading behind
  var laps=L.laps,cx=x+118,cw=w-118-44,top=y+104,bot=y+h-96,ch=bot-top,tot=0;laps.forEach(function(l){tot+=Math.max(l.d,0.02)});
  var ps=laps.map(function(l){return l.p}),pmin=Math.min.apply(null,ps),pmax=Math.min(Math.max.apply(null,ps),pmin+240),step=(pmax-pmin+24)>200?60:30,lo=Math.floor((pmin-8)/step)*step,hi=pmax+12;
  function Y(pc){return top+(Math.min(Math.max(pc,lo),hi)-lo)/(hi-lo)*ch}
  var hrs=laps.map(function(l){return l.hr}).filter(Boolean),hmin=hrs.length?Math.min.apply(null,hrs):0,hmax=hrs.length?Math.max.apply(null,hrs):0,cum=0,seg=[];
  laps.forEach(function(l){var x0=cx+cum/tot*cw;cum+=Math.max(l.d,0.02);seg.push({l:l,x0:x0,x1:cx+cum/tot*cw})});
  if(hrs.length>1){var hg=ctx.createLinearGradient(0,top,0,bot);hg.addColorStop(0,"rgba(122,167,255,.34)");hg.addColorStop(1,"rgba(122,167,255,.04)");ctx.fillStyle=hg;ctx.beginPath();ctx.moveTo(cx,bot);
    seg.forEach(function(q){var hh=q.l.hr?bot-ch*(0.15+0.7*(q.l.hr-hmin)/((hmax-hmin)||1)):bot;ctx.lineTo(q.x0,hh);ctx.lineTo(q.x1,hh)});ctx.lineTo(cx+cw,bot);ctx.closePath();ctx.fill()}
  ctx.font="500 24px "+FD;ctx.textAlign="right";
  for(var tk=Math.ceil(lo/step)*step;tk<=hi;tk+=step){var gy=Y(tk);ctx.strokeStyle="rgba(255,255,255,.12)";ctx.lineWidth=2;ctx.setLineDash([6,8]);ctx.beginPath();ctx.moveTo(cx,gy);ctx.lineTo(cx+cw,gy);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle="rgba(255,255,255,.5)";ctx.fillText(fmtMS(tk),cx-14,gy+8)}
  ctx.textAlign="left";
  ctx.strokeStyle="rgba(255,255,255,.35)";ctx.lineWidth=2;seg.forEach(function(q,k2){if(k2){var a=seg[k2-1];ctx.beginPath();ctx.moveTo(q.x0,Y(a.l.p));ctx.lineTo(q.x0,Y(q.l.p));ctx.stroke()}});
  seg.forEach(function(q){var hard=!q.l.whole&&q.l.d>=0.4&&q.l.p<330*K,yy=Y(q.l.p);ctx.strokeStyle=hard?"#9d94ff":"rgba(255,255,255,.8)";ctx.lineWidth=hard?9:5;ctx.lineCap="round";ctx.beginPath();ctx.moveTo(q.x0+4,yy);ctx.lineTo(q.x1-4,yy);ctx.stroke();ctx.lineCap="butt"});
  var lastX=-999;seg.forEach(function(q){var hard=!q.l.whole&&q.l.d>=0.4&&q.l.p<330*K,mid=(q.x0+q.x1)/2,yy=Y(q.l.p),wd=q.x1-q.x0;if(!hard&&wd<64)return;if(mid-lastX<78)return;
    ctx.textAlign="center";ctx.font=(hard?"800 32px ":"600 24px ")+FD;ctx.fillStyle=hard?"#fff":"rgba(255,255,255,.7)";var ly=yy-18<top-6?yy+40:yy-18;ctx.fillText(fmtMS(q.l.p),Math.min(Math.max(mid,cx+40),cx+cw-40),ly);ctx.textAlign="left";lastX=mid});
  ctx.fillStyle="rgba(255,255,255,.55)";ctx.font="500 24px "+FB;ctx.fillText("0km",cx,bot+32);ctx.textAlign="right";ctx.fillText((Math.round(tot*10)/10)+"km",cx+cw,bot+32);ctx.textAlign="left";
  ctx.font="500 24px "+FB;ctx.fillStyle="#ff9a5c";ctx.fillRect(cx,y+h-30,34,6);ctx.fillText("랩 평균 페이스",cx+44,y+h-20);if(hrs.length>1){ctx.textAlign="right";ctx.fillStyle="#9cc0ff";ctx.fillText("심박 "+hmin+"~"+hmax,cx+cw,y+h-20);ctx.fillStyle="rgba(122,167,255,.45)";var tw=ctx.measureText("심박 "+hmin+"~"+hmax).width;ctx.fillRect(cx+cw-tw-44,y+h-36,32,18);ctx.textAlign="left"}
}
```

## 4. 교체할 코드 — 대회 주 카드
`var RCHK=[…];`와 `function raceWeekCard(){…}`를 아래로 **통째로 교체**해.
```js
var RCHK=[["carb","카보로딩","탄수 체중×8~10g, 섬유·지방은 줄이기"],["plan","레이스 전략 확인","구간 페이스·보급 시점 다시 보기"],["kit","준비물","배번·칩, 젤 4~6개, 바셀린, 레이스 복장·신발"],["sleep","수면","대회 전 이틀은 7시간 이상"],["route","출발 동선","집결 시간·교통·물품 보관 확인"]];
function raceWeekCard(){if(sel!==T)return '';var nx=null;for(var k=0;k<=7;k++){var ds=addDays(T,k),ix=idxOf(ds);if(ix>=0&&ix<TOTAL&&planAt(ix)[0]==="x"){nx=ds;break}}
  (S.races||[]).forEach(function(rc){if(!nx&&rc.date>=T&&rc.date<=addDays(T,7))nx=rc.date});if(!nx)return '';
  var dd=Math.round((p(nx)-p(T))/864e5),rc=(S.races||[]).filter(function(x){return x.date===nx})[0],nm=rc?rc.name:(nx===RACE?'서울마라톤':(function(){var ix=idxOf(nx),tx=ix>=0&&ix<TOTAL?String(planAt(ix)[1]).split("\n")[0].trim():"";return tx&&tx.length<=20?tx:'대회'})()),ck=(S.raceChk||{})[nx]||{},n=RCHK.filter(function(x){return ck[x[0]]}).length;
  var op=foldOpen("rw",false),nx2=RCHK.filter(function(x){return !ck[x[0]]})[0],pct=Math.round(n/RCHK.length*100),IC={carb:"🍝",plan:"🗺️",kit:"🎒",sleep:"😴",route:"🚇"},WHEN={carb:"D-2~D-1",plan:"D-3까지",kit:"D-1",sleep:"D-2부터",route:"D-1"};
  var gl=rc&&rc.goal?fmtT(rc.goal):(nx===RACE&&S.goal?fmtT(S.goal.sec):""),dw=DOW[(new Date(p(nx)).getUTCDay()+6)%7];
  var hd='<button type="button" class="rwh" data-rwt aria-expanded="'+op+'" aria-label="'+esc(nm)+' 준비 '+(op?'접기':'펼치기')+'"><span class="rwd"><small>'+(dd?'D-':'D-DAY')+'</small><b class="num">'+(dd?dd:'🏁')+'</b></span><span class="rwt"><small>'+md(nx)+' ('+dw+')'+(gl?' · 목표 '+gl:'')+'</small><b>'+esc(nm)+'</b><em>'+(n===RCHK.length?'✓ 준비 완료':'다음 · '+esc(nx2[1]))+'</em></span><span class="rwring" style="--p:'+pct+'"><i class="num">'+n+'<small>/'+RCHK.length+'</small></i></span><span class="rwchev" aria-hidden="true"></span></button>';
  if(!op)return '<div class="card raceweek rwc rwfold">'+hd+'</div>';
  return '<div class="card raceweek rwc">'+hd+'<ul class="rwl">'+RCHK.map(function(x){var on=!!ck[x[0]];return '<li><label class="rwi'+(on?' on':'')+'"><input type="checkbox" class="rwin" data-rck="'+nx+'|'+x[0]+'"'+(on?' checked':'')+'><span class="rwic" aria-hidden="true">'+IC[x[0]]+'</span><span class="rwtx"><b>'+x[1]+'<em>'+WHEN[x[0]]+'</em></b><small>'+x[2]+'</small></span><span class="rwck" aria-hidden="true"></span></label></li>'}).join('')+'</ul><button class="rwgo" type="button" data-goto="pace"><span>🏁 레이스 전략 보기</span><small>구간 페이스 · 보급 · 통과 시간</small><span class="rwarr" aria-hidden="true">→</span></button></div>'}
```

`[data-rck]` 체크박스를 바인딩하는 곳(오늘 탭) 바로 위에 아래 한 줄을 넣어.
```js
view.querySelectorAll("[data-rwt]").forEach(function(b){b.onclick=function(){foldSet("rw",!foldOpen("rw",false));keepIf()}});
```

`[data-goto]` 버튼 처리에 `b.closest(".raceweek")`면 페이스 탭을 레이스 전략 화면으로 여는 분기가 있는지 확인해. 없으면 `data-goto="pace"`를 눌렀을 때 페이스 탭의 레이스 전략 화면이 열리게 해 줘.

## 5. 추가할 CSS (메인 `<style>` 끝)
기존 `.raceweek{background:…}` 규칙은 그대로 둬도 돼. 아래 `.card.rwc` 규칙이 덮어써.
```css
.card.rwc{padding:0;overflow:hidden;background:var(--card)!important}
.rwh{all:unset;box-sizing:border-box;cursor:pointer;width:100%;display:grid;grid-template-columns:auto 1fr auto 18px;align-items:center;gap:12px;padding:14px 14px 14px 12px;background:linear-gradient(120deg,var(--navy),color-mix(in srgb,var(--accent) 60%,var(--navy)));color:#fff}
.rwh:focus-visible{outline:2px solid var(--accent);outline-offset:-3px}
.rwd{width:58px;height:58px;border-radius:16px;display:flex;flex-direction:column;align-items:center;justify-content:center;background:rgba(255,255,255,.13);box-shadow:inset 0 0 0 1px rgba(255,255,255,.2)}
.rwd small{font-size:10px;font-weight:800;letter-spacing:.08em;opacity:.75}.rwd b{font-size:26px;line-height:1;font-family:var(--f-display)}
.rwt{display:flex;flex-direction:column;min-width:0;gap:1px}.rwt small{font-size:11.5px;opacity:.75}.rwt b{font-size:16.5px;font-family:var(--f-display);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rwt em{font-style:normal;font-size:12px;font-weight:700;color:#d9d4ff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rwring{width:46px;height:46px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(#b8b1ff calc(var(--p)*1%),rgba(255,255,255,.16) 0)}
.rwring i{width:36px;height:36px;border-radius:50%;display:flex;align-items:baseline;justify-content:center;padding-top:9px;box-sizing:border-box;font-style:normal;font-weight:800;font-size:14px;background:color-mix(in srgb,var(--accent) 45%,var(--navy))}.rwring small{font-size:10px;opacity:.7}
.rwchev{width:8px;height:8px;border-right:2px solid rgba(255,255,255,.75);border-bottom:2px solid rgba(255,255,255,.75);transform:translateY(-2px) rotate(45deg);transition:transform .2s}
.rwh[aria-expanded="true"] .rwchev{transform:translateY(2px) rotate(-135deg)}
.rwl{list-style:none;margin:0;padding:12px 12px 4px;display:flex;flex-direction:column;gap:6px}
.rwi{position:relative;display:grid;grid-template-columns:36px 1fr 24px;align-items:center;gap:10px;padding:10px 12px 10px 10px;border-radius:14px;background:var(--bg);cursor:pointer;transition:background .15s,box-shadow .15s}
.rwi.on{background:color-mix(in srgb,var(--ok) 10%,var(--card));box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--ok) 40%,transparent)}
.rwin{position:absolute;opacity:0;pointer-events:none}.rwin:focus-visible~.rwck{outline:2px solid var(--accent);outline-offset:2px}
.rwic{width:36px;height:36px;border-radius:11px;display:grid;place-items:center;font-size:19px;background:var(--card)}
.rwtx{display:flex;flex-direction:column;min-width:0;gap:1px}.rwtx b{font-size:14px;display:flex;align-items:center;gap:6px}.rwtx b em{font-style:normal;font-size:10.5px;font-weight:700;color:var(--accent);background:var(--accent-soft);padding:1px 7px;border-radius:99px}
.rwtx small{font-size:12px;color:var(--muted);line-height:1.4}
.rwi.on .rwtx b{color:var(--muted);text-decoration:line-through;text-decoration-color:color-mix(in srgb,var(--muted) 60%,transparent)}
.rwck{width:22px;height:22px;border-radius:50%;box-shadow:inset 0 0 0 2px var(--line);background:var(--card);display:grid;place-items:center}
.rwi.on .rwck{background:var(--ok);box-shadow:none}.rwi.on .rwck:after{content:"";width:5px;height:9px;border:solid #fff;border-width:0 2px 2px 0;transform:translateY(-1px) rotate(45deg)}
.rwgo{all:unset;box-sizing:border-box;cursor:pointer;display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:8px;width:calc(100% - 24px);margin:6px 12px 12px;padding:12px 14px;border-radius:14px;background:var(--accent-soft);color:var(--accent)}
.rwgo span:first-child{font-weight:800;font-size:14px}.rwgo small{font-size:11.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.rwarr{font-weight:800}
.rwgo:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
```

## 6. 확인하고 알려줘
1. 문법 오류 없이 페이지가 열리는지
2. 같은 날 기록이 2개인 날짜로 인스타 스토리를 열었을 때
   - 그래프에 '2회차' 경계가 있고, 각 랩 평균선이 자기 회차 구간에 있는지
   - 랩표에 '1회차'·'2회차' 표시가 있고, 랩 없는 회차는 '전체' 한 줄로 나오는지
   - AI 멘트가 하루 전체 기준인지
3. 기록이 1개인 날은 예전과 똑같이 나오는지
4. 대회 7일 전부터 오늘 탭 대회 주 카드가 기본 접힘이고, 펼침·체크·접힘이 되는지
5. 게시 후 저장된 권한 선언이 이전과 같은지
