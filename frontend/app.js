function dashboard() {
  return {
    /* ===== 页面与数据状态 ================================================== */
    page:'overview',navLogsOpen:false,clock:'',
    securitySummary:null,securityRows:[],securityType:'ipslog',securityBefore:null,securityNext:null,securityBack:[],securityDetail:'',securityError:'',securityBusy:false,securityTimer:null,securityUpdated:'',
    summary:{total:0,by_platform:{Linux:0,Windows:0},categories:[]},categories:[],clients:[],logs:[],evidence:[],logKeys:[],logPlatform:'Linux',logCategory:'authentication_session',keyword:'',heartbeatMetrics:[],evidenceMetrics:[],overviewMetrics:[],
    // 心跳计数：WebSocket 快照与 REST 降级共用同一份（图表与指标都从这里取数）
    hbCounts:{total:0,online:0,delayed:0,offline:0},
    // 图表数据（全部来自后端聚合接口，前端不自己累积历史）
    trend:null,trendNote:'',history:null,historyNote:'',
    // 概览「哈希存证」面板：/api/v1/evidence/summary（30 秒一轮）。失败时接口返回 503，
    // 此时必须显示「不可用」而不是 0——把故障显示成「0 条存证」会让人以为存证被清了。
    hashTotal:0,hashStatuses:null,hashMetrics:[],hashError:'',hashNote:'',hashCheckedAt:'',hashBusy:false,
    // 存证页的筛选与校验展示：verify 传 HASH_MISMATCH / RAW_MISSING / VALID 可只看某一类。
    // evidenceSource 显示实际读取的库表，避免「看的不是同一个库」这类误判。
    // evidenceTab 是「存证中心」的功能分区：overview / ledger / archive / policy。
    evidenceVerify:'',evidenceKeyword:'',evidenceSource:'',evidenceTampered:0,
    evidencePlatform:'',evidenceStart:'',evidenceEnd:'',
    evidenceTab:'overview',
    /* 心跳延迟曲线的绘制参数
     * DELAY_AXIS_FLOOR：纵轴下限（秒）——延迟只有 1 秒时也要有 5 秒的画布高度，
     *   否则 0.2 秒的正常起伏就会占满 10% 的高度，看起来像在剧烈波动。
     * 曲线本身**不做任何平滑**：按需求直接画原始折线（见 drawDelayChart 注释）。 */
    DELAY_AXIS_FLOOR:5,
    statsPlatform:'',statsRange:'24h',statsTrend:null,statsHeat:null,statsRank:null,statsTrendNote:'',statsNote:'',
    /* ===== 连接与定时器 =================================================== */
    wsStatus:'connecting',ws:null,wsRetry:0,wsTimer:null,wsLastMsg:0,wsWatchTimer:null,pollTimer:null,summaryTimer:null,trendTimer:null,historyTimer:null,pageTimer:null,statsTimer:null,hashTimer:null,summaryBusy:false,summarySig:'',trendBusy:false,historyBusy:false,statsBusy:false,
    /* ===== 图表实例注册表 ================================================= */
    charts:{},painted:{},resizeTimer:null,countUpTimer:null,chartNote:'',
    // 分页状态：融合日志与存证列表都是一页 30 条
    PAGE_SIZE:30,logPage:1,logPages:1,logTotal:0,evidencePage:1,evidencePages:1,evidenceTotal:0,
    // 存证归档（导出 + 删除）
    archiveStart:'',archiveEnd:'',archiveBusy:false,archiveMessage:'',archiveError:false,archiveMonths:3,
    // 系统维护页：触发服务端代码更新（停服 → 原地 git 更新 → 重启），期间需轮询 /health 等恢复
    updateBusy:false,updatePolling:false,updateMessage:'',updateError:false,
    fmt(v){return Number(v||0).toLocaleString('zh-CN')},
    pad(n){return String(n).padStart(2,'0')},

    bootWarning(html){const el=document.getElementById('lf-boot-warning');if(!el)return;el.style.display='';el.innerHTML=html},
    warn(title,err){
      const detail=(err&&err.message)?err.message:String(err||'');
      this.bootWarning(title+'：'+detail
        +'<br><span style="color:#666">若页面长期转圈、点击无反应，通常是浏览器对同一地址的 6 条 HTTP 连接已被占满：请关闭多余标签页后重开本页（Ctrl+F5）。</span>');
    },

    // 8 秒超时：连接被占满时 fetch 会永久排队，加超时才能失败并给出提示
    async api(path){
      const ctl=new AbortController();const timer=setTimeout(()=>ctl.abort(),8000);
      try{
        const r=await fetch(path,{signal:ctl.signal});
        if(!r.ok)throw Error('HTTP '+r.status);
        const j=await r.json();return j.data??j;
      }catch(e){
        throw (e&&e.name==='AbortError')?Error('请求超时（8s）'):e;
      }finally{clearTimeout(timer)}
    },

    /* ===== 时区与时间格式 =================================================
     * 后端所有时间桶/采样点都是 UTC（`...Z`），本地化只在前端做；
     * 直接展示 ISO 字符串会让趋势图与热力图整体偏 8 小时。
     */
    fmtTime(iso){const d=new Date(iso);return isNaN(d.getTime())?'-':d.toLocaleString('zh-CN',{hour12:false})},
    // kind: HM 时:分 / HMS 时:分:秒 / MD 月-日 / MDH 月-日 时:00
    fmtClock(iso,kind){
      const d=new Date(iso);if(isNaN(d.getTime()))return String(iso);
      const hm=this.pad(d.getHours())+':'+this.pad(d.getMinutes());
      if(kind==='HMS')return hm+':'+this.pad(d.getSeconds());
      if(kind==='MD')return this.pad(d.getMonth()+1)+'-'+this.pad(d.getDate());
      if(kind==='MDH')return this.pad(d.getMonth()+1)+'-'+this.pad(d.getDate())+' '+this.pad(d.getHours())+':00';
      return hm;
    },
    axisLabelOf(iso,bucket){return bucket==='day'?this.fmtClock(iso,'MD'):bucket==='minute'?this.fmtClock(iso,'HM'):this.fmtClock(iso,'MDH')},
    // 「数据有多新」必须可见：接口带 generatedAt/cached/ageMs 就是为了这一行
    freshness(d){
      if(!d||!d.generatedAt)return '数据新鲜度未知';
      const stamp=this.fmtTime(d.generatedAt);
      if(!d.cached)return '服务端刚刚聚合（'+stamp+'）';
      const age=Math.max(0,Math.round(Number(d.ageMs||0)/1000));
      return '服务端缓存 '+age+' 秒前聚合（'+stamp+'）';
    },

    /* ===== 导航（二级菜单 + hash 深链）==================================== */
    pageFromHash(){
      const raw=String(location.hash||'').replace(/^#\/?/,'');
      return ['overview','heartbeat','logs','log-stats','evidence','maintenance','security'].includes(raw)?raw:'';
    },
    go(p,silent){
      if(!['overview','heartbeat','logs','log-stats','evidence','maintenance','security'].includes(p))return;
      this.page=p;
      if(p==='logs'||p==='log-stats')this.navLogsOpen=true;
      try{history.replaceState(null,'','#/'+p)}catch(e){location.hash='#/'+p}
      if(!silent)this.onPageEnter();
    },
    // 进入页面才取数并画图：隐藏区块宽高为 0，画出来就是 0×0
    onPageEnter(){
      const p=this.page;
      if(p==='overview'){this.refreshSummary();this.loadTrend();this.loadEvidenceSummary()}
      else if(p==='heartbeat'){if(!this.clients.length)this.loadHeartbeats();this.loadHistory();this.drawHeartbeatCharts()}
      else if(p==='logs'){this.loadLogs()}
      else if(p==='log-stats'){this.loadLogStats()}
      else if(p==='evidence'){this.loadEvidence()}
      else if(p==='security'){this.loadSecurity()}
      this.afterPaint(()=>this.redrawVisible());
    },
    // Alpine 的 x-show 是异步生效的，必须等它把 display 改回来再画
    afterPaint(fn){if(this.$nextTick)this.$nextTick(()=>setTimeout(fn,0));else setTimeout(fn,0)},
    redrawVisible(){
      if(this.page==='overview'){this.drawChart();this.drawOverviewTrend();this.drawPlatformGauge()}
      else if(this.page==='heartbeat')this.drawHeartbeatCharts();
      else if(this.page==='log-stats')this.drawStatsAll();
    },

    /* ===== 图表基础设施 ===================================================
     * 三条铁律（历史上踩过）：① 复用实例，不能每次刷新都 echarts.init；
     * ② 区块被 x-show 隐藏时容器宽高为 0，必须跳过绘制；③ resize 150ms 防抖。
     */
    getChart(id){
      const el=document.getElementById(id);
      if(!el||!window.echarts||!el.clientWidth||!el.clientHeight)return null;
      let chart=this.charts[id];
      // 实例被销毁或容器换掉时必须重建，并**同时丢掉绘制指纹**：
      // 否则新实例会因为「数据没变」而被 paint() 判为无需绘制，留下空白图
      if(chart&&(chart.isDisposed()||chart.getDom()!==el)){delete this.charts[id];delete this.painted[id];chart=null}
      if(!chart){const lib=window.echarts;chart=lib.getInstanceByDom(el)||lib.init(el);this.charts[id]=chart}
      return chart;
    },
    // 只读容器宽度（不创建实例）：窄屏/宽屏分支与「隐藏时不画」都靠它判断
    chartWidth(id){const el=document.getElementById(id);return (el&&el.clientWidth)||0},
    disposeCharts(){
      Object.keys(this.charts).forEach(id=>{try{this.charts[id].dispose()}catch(e){}});
      this.charts={};this.painted={};
    },
    resizeAll(){
      clearTimeout(this.resizeTimer);
      // 容器尺寸变化后必须重画：百分比半径、图例位置、窄屏分支都依赖 clientWidth。
      // 隐藏区块（宽高为 0）不能 resize——那会把实例尺寸改成 0，等它再显示出来就是一张空白图。
      this.resizeTimer=setTimeout(()=>{
        Object.keys(this.charts).forEach(id=>{
          const c=this.charts[id];if(!c||c.isDisposed())return;
          const el=c.getDom&&c.getDom();if(el&&(!el.clientWidth||!el.clientHeight))return;
          c.resize();
        });
        this.redrawVisible();
      },150);
    },
    /* 绘制指纹。
     * `sig` 是整个 option 的 JSON（函数会被 JSON 丢掉，因此凡是「值」都必须写进 option 字段，
     * 不能只藏在 formatter 闭包里——环图中心数字就是这样放进 data[0].name 的）。
     * `structure` 是 series 的数量/名称/类型：结构变了必须 replaceMerge，否则旧序列会留在图上。 */
    paintKeys(option){
      const series=option.series||[];
      return {
        structure:series.length+'|'+series.map(s=>(s.name||'')+':'+(s.type||'')).join(','),
        sig:JSON.stringify(option)
      };
    },
    /* 所有图表的唯一绘制出口。
     * WS 每 5 秒推一次快照、概览每 2 秒刷一次汇总：如果每次都无脑 setOption，ECharts 会把每张图
     * 当成新图重播入场动画（从 0 长出来），看起来就是整页一直在闪。所以：
     *   ① 数据+布局没变 → 一个字节都不重画（watchdog 每轮都推快照，多数轮次数据是相同的）；
     *   ② 只有数据变 → 合并更新，ECharts 只做值过渡（animationDurationUpdate），不重播入场动画；
     *   ③ 只有 series 结构变（客户端增减、平台筛选切换）才 replaceMerge。
     * resize 也只在实例尺寸与容器不一致时才做——resize 本身会整块重绘。 */
    paint(id,option){
      const chart=this.getChart(id);
      if(!chart)return false;
      const el=chart.getDom?chart.getDom():null;
      if(el&&chart.getWidth&&(Math.abs(chart.getWidth()-el.clientWidth)>0.5||Math.abs(chart.getHeight()-el.clientHeight)>0.5))chart.resize();
      const keys=this.paintKeys(option);
      const prev=this.painted[id];
      if(prev&&prev.sig===keys.sig)return false;
      const merge=!!prev&&prev.structure===keys.structure;
      chart.setOption(option,merge?undefined:{replaceMerge:['series','title','graphic','legend','visualMap']});
      this.painted[id]=keys;
      return true;
    },
    // 渐变色必须经 echarts.graphic，纯色字符串在面积图上是平涂，企业看板要的是层次
    rgba(hex,alpha){
      const h=String(hex||'').replace('#','');
      const full=h.length===3?h.split('').map(c=>c+c).join(''):h;
      const n=parseInt(full,16);
      if(isNaN(n))return hex;
      return 'rgba('+[(n>>16)&255,(n>>8)&255,n&255].join(',')+','+alpha+')';
    },
    areaGradient(color,e){
      const lib=e||window.echarts;
      if(!lib||!lib.graphic||!lib.graphic.LinearGradient)return this.rgba(color,0.22);
      return new lib.graphic.LinearGradient(0,0,0,1,[{offset:0,color:this.rgba(color,0.30)},{offset:1,color:this.rgba(color,0)}]);
    },
    emptyTitle(text,top){return {show:true,text:text||'暂无数据',left:'center',top:top||'middle',textStyle:{color:'#9aa8bd',fontSize:13,fontWeight:'normal'}}},
    // 环图/半环图中心文字：用「半径 0 的哑系列 + label position:center」而不是 graphic 的百分比定位，
    // 位置由 ECharts 自己算，任何容器尺寸下都居中且不会压到圆环。
    // 系列名固定为 centerText（结构指纹因此稳定，数值变化走合并更新而不是重播动画），
    // 文字放在 data[0].name 里——它是 option 的可见字段，否则「数据没变就不重画」会漏掉数字变化。
    centerPie(text,sub,center,size,show){
      return {name:'centerText',type:'pie',radius:['0%','0%'],center:center||['50%','50%'],silent:true,animation:false,
        tooltip:{show:false},emphasis:{disabled:true},labelLine:{show:false},
        label:{show:show!==false,position:'center',
          formatter:()=>sub?('{v|'+text+'}\n{s|'+sub+'}'):('{v|'+text+'}'),
          rich:{v:{fontSize:size||22,fontWeight:700,color:'#19263d',lineHeight:(size||22)+4},s:{fontSize:11,color:'#728197',lineHeight:15}}},
        data:[{value:1,name:text}]};
    },
    splitLine(){return {lineStyle:{color:'#eef2f8',type:'dashed'}}},
    axisLine(){return {lineStyle:{color:'#e4eaf3'}}},
    tooltipAxis(unit){return {trigger:'axis',axisPointer:{type:'line',lineStyle:{color:'#b9c3d3',type:'dashed'}},valueFormatter:v=>unit?v+' '+unit:this.fmt(v)}},

    /* ===== 数字滚动（count-up，纯 JS 无依赖）============================== */
    setMetrics(key,defs){
      const prev=this[key]||[];
      this[key]=defs.map(d=>{
        const old=prev.find(p=>p.label===d.label)||{};
        // 目标值没变就保持原动画状态：否则 WS 每 5 秒推一次相同快照都会重启动画，数字一直在抖
        if(typeof d.num==='number'&&old.num===d.num)return Object.assign({},d,{from:old.from,t0:old.t0});
        // 首次出现时从 0 滚到真实值（这才是「数字滚动」），之后从「上一次显示到的数字」继续滚
        const from=(typeof old.num==='number')?old.num:(typeof d.num==='number'?0:d.num);
        return Object.assign({},d,{from:from,t0:Date.now()});
      });
      if(!this.countUpTimer)this.countUpTimer=requestAnimationFrame(()=>this.countStep());
    },
    countStep(){
      const now=Date.now();let active=false;
      ['overviewMetrics','heartbeatMetrics','evidenceMetrics','hashMetrics'].forEach(key=>{
        (this[key]||[]).forEach(m=>{
          if(typeof m.num!=='number')return;
          const span=600,p=Math.min(1,(now-(m.t0||now))/span),eased=1-Math.pow(1-p,3);
          const from=(typeof m.from==='number')?m.from:m.num;
          m.value=this.fmt(Math.round(from+(m.num-from)*eased));
          if(p<1)active=true;
        });
      });
      this.countUpTimer=active?requestAnimationFrame(()=>this.countStep()):null;
    },

    /* ===== 启动与连接 ===================================================== */
    // 只保留 WebSocket 这一条长连接：HTML/JS 之外不再占用浏览器同源连接额度
    closeStreams(){try{if(this.ws)this.ws.close()}catch(e){}},

    async init(){
      window.addEventListener('pagehide',()=>{clearInterval(this.securityTimer);this.closeStreams();this.disposeCharts()});
      // 图表只按容器初始尺寸绘制，窗口变化后必须重画，否则图形与图例错位
      window.addEventListener('resize',()=>this.resizeAll());
      const fromHash=this.pageFromHash();
      if(fromHash)this.page=fromHash;
      if(this.page==='logs'||this.page==='log-stats')this.navLogsOpen=true;
      window.addEventListener('hashchange',()=>{const p=this.pageFromHash();if(p&&p!==this.page)this.go(p)});
      this.tick();setInterval(()=>this.tick(),1000);
      // 概览首屏：先拿汇总（服务端渲染已填过一次，这里立即刷新成最新）
      await this.refreshSummary();
      try{this.categories=await this.api('/api/v1/logs/categories')}catch(e){}
      try{const a=await this.api('/api/v1/archive/preview');if(a&&a.retentionMonths!=null)this.archiveMonths=a.retentionMonths}catch(e){}
      this.connectMonitorWs();this.startWsWatchdog();
      this.onPageEnter();
      // WebSocket 断开时退化为 5 秒轮询；概览 2 秒汇总 + 10 秒趋势；心跳历史 10 秒；
      // 日志/存证页 15 秒；统计页与存证摘要各 30 秒。每个定时器都先看「当前页可见」再看 busy 标志，
      // 慢响应不会被下一次触发叠成请求队列。
      this.pollTimer=setInterval(()=>{if(this.wsStatus!=='connected')this.loadHeartbeats()},5000);
      this.summaryTimer=setInterval(()=>{if(this.page==='overview'&&!this.summaryBusy)this.refreshSummary()},2000);
      this.trendTimer=setInterval(()=>{if(this.page==='overview'&&!this.trendBusy)this.loadTrend()},10000);
      this.hashTimer=setInterval(()=>{if(this.page==='overview')this.loadEvidenceSummary()},30000);
      this.historyTimer=setInterval(()=>{if(this.page==='heartbeat'&&!this.historyBusy)this.loadHistory()},10000);
      this.pageTimer=setInterval(()=>{if(this.page==='logs')this.loadLogs();else if(this.page==='evidence')this.loadEvidence()},15000);
      this.statsTimer=setInterval(()=>{if(this.page==='log-stats'&&!this.statsBusy)this.loadLogStats()},30000);
      this.securityTimer=setInterval(()=>{if(this.page==='security')this.loadSecurity()},10000);
    },
    tick(){this.clock=new Date().toLocaleString('zh-CN',{hour12:false})},

    /* ===== 侧栏「服务连接」卡片 =========================================== */
    // 数据库部分由 HTMX 每 30 秒替换 /partials/db-status（服务端真实 SELECT 1 的结果）；
    // 这里只负责浏览器自己的实时通道状态，两者拼成一张卡片。
    wsTone(){return this.wsStatus==='connected'?'ok':this.wsStatus==='connecting'?'warn':'bad'},
    wsLabel(){return this.wsStatus==='connected'?'已连接':this.wsStatus==='connecting'?'连接中…':'已断开'},
    wsHint(){return this.wsStatus==='connected'?'5 秒推送':'5 秒轮询'},

    realtimeMetric(){
      const ok=this.wsStatus==='connected';
      return {label:'实时通道',value:ok?'正常':'降级轮询',hint:ok?'WebSocket 已连接':'WebSocket 断开，改用 5 秒轮询'};
    },
    syncRealtimeMetric(){
      if(!this.overviewMetrics||!this.overviewMetrics.length)return;
      const i=this.overviewMetrics.findIndex(m=>m.label==='实时通道');
      if(i>=0)this.overviewMetrics[i]=this.realtimeMetric();else this.overviewMetrics.push(this.realtimeMetric());
    },

    /* ===== 运行概览 ======================================================= */
    async refreshOverview(){this.refreshSummary();this.loadTrend(true);this.loadEvidenceSummary(true)},
    /* 概览「哈希存证」面板：只取摘要（COUNT(*)）不做逐条复算。
     * 失败时接口返回 503，这里必须把面板切成「不可用」并保留错误原因——
     * 显示成 0 条会让人以为存证被清空了，这正是存证接口当初改用 503 的原因。 */
    async loadEvidenceSummary(force){
      if(this.hashBusy)return;
      if(!force&&this.page!=='overview')return;
      this.hashBusy=true;
      try{
        const s=await this.api('/api/v1/evidence/summary');
        const st=s.statuses||{};
        this.hashTotal=Number(s.total||0);
        this.hashStatuses=st;
        this.hashError='';
        this.hashCheckedAt=new Date().toLocaleTimeString('zh-CN',{hour12:false});
        this.setMetrics('hashMetrics',[
          {label:'存证总数',value:this.fmt(s.total),num:Number(s.total||0)},
          {label:'一致',value:this.fmt(st.VALID),num:Number(st.VALID||0),tone:'ok'},
          {label:'哈希异常',value:this.fmt(st.HASH_MISMATCH),num:Number(st.HASH_MISMATCH||0),tone:'bad'},
          {label:'原始缺失',value:this.fmt(st.RAW_MISSING),num:Number(st.RAW_MISSING||0),tone:'warn'}
        ]);
        this.hashNote='更新于 '+this.hashCheckedAt+'；摘要按 sha256(时间+原文) 在入库前计算并写入。'
          +'接口已逐条复算（原始缺失不计入异常，仅说明无从复算）。';
      }catch(e){
        this.hashTotal=0;this.hashStatuses=null;this.hashMetrics=[];this.hashCheckedAt='';
        this.hashError='存证库查询失败（'+(e&&e.message?e.message:e)+'）：这不代表没有存证，请检查 log_hash 库连接。';
      }finally{this.hashBusy=false}
    },
    // 指标卡的数字滚动由 countStep 更新，这里只把当前显示值取出来给模板用（找不到就显示 —，不要显示 0）
    metricValue(key,label){const m=(this[key]||[]).find(x=>x.label===label);return m?m.value:'—'},
    hashTiles(){return (this.hashMetrics||[]).filter(m=>m.label!=='存证总数')},
    hashCount(name){return Number((this.hashStatuses||{})[name]||0)},
    hashValidPct(){return this.hashTotal?Math.round(this.hashCount('VALID')/this.hashTotal*1000)/10:0},
    hashBar(){
      const total=this.hashTotal;
      if(!this.hashStatuses||!total)return {ok:0,warn:0,bad:0};
      const pct=v=>Math.round(this.hashCount(v)/total*1000)/10;
      return {ok:pct('VALID'),warn:pct('RAW_MISSING'),bad:pct('HASH_MISMATCH')};
    },
    hashTone(){
      if(this.hashError)return 'bad';
      if(!this.hashTotal)return 'muted';
      if(this.hashCount('HASH_MISMATCH')>0)return 'bad';
      if(this.hashCount('RAW_MISSING')>0)return 'warn';
      return 'ok';
    },
    hashPillText(){
      if(this.hashError)return '存证库不可用';
      if(!this.hashTotal)return '暂无存证';
      if(this.hashCount('HASH_MISMATCH')>0)return '哈希异常 '+this.fmt(this.hashCount('HASH_MISMATCH'))+' 条';
      if(this.hashCount('RAW_MISSING')>0)return '原始缺失 '+this.fmt(this.hashCount('RAW_MISSING'))+' 条';
      return '完整率 '+(this.hashValidPct()>=100?100:this.hashValidPct())+'%';
    },
    // 概览统计。2 秒一次意味着每 2 秒对 12 张表做一轮 COUNT(*)（实测 286–497 ms），因此：
    // ① summaryBusy 保证同一时刻只有一条在飞（慢响应不会被下一次定时器叠加成队列）；
    // ② 只在概览页可见时由定时器触发（其他页面看不到这些数字，不必付这份数据库开销）；
    // ③ 数据没变就不重画环形图（否则每 2 秒白跑一次 setOption）。
    async refreshSummary(){
      if(this.summaryBusy)return;
      this.summaryBusy=true;
      try{
        const s=await this.api('/api/v1/logs/summary');
        const sig=JSON.stringify([s.total,s.by_platform,s.categories]);
        const changed=sig!==this.summarySig;
        this.summary=s;this.summarySig=sig;
        this.setMetrics('overviewMetrics',[
          {label:'日志总量',value:this.fmt(s.total),num:Number(s.total||0),hint:'实时刷新'},
          {label:'Linux 日志',value:this.fmt(s.by_platform.Linux),num:Number(s.by_platform.Linux||0),hint:'审计日志'},
          {label:'Windows 日志',value:this.fmt(s.by_platform.Windows),num:Number(s.by_platform.Windows||0),hint:'事件日志'},
          this.realtimeMetric()
        ]);
        if(changed)this.drawChart();
        this.drawPlatformGauge();
      }catch(e){this.warn('概览统计加载失败',e)}
      finally{this.summaryBusy=false}
    },
    // 概览趋势：最近 60 分钟按分钟桶（10 秒轮询，服务端 5 秒 TTL）
    async loadTrend(force){
      if(this.trendBusy)return;
      if(!force&&this.page!=='overview')return;
      this.trendBusy=true;
      try{
        const d=await this.api('/api/v1/logs/trend?range=1h&bucket=minute');
        this.trend=d;
        this.drawOverviewTrend();
        const pts=(d&&d.points)||[];
        const sum=pts.reduce((a,p)=>a+Number(p.total||0),0);
        this.trendNote='最近 60 分钟按分钟桶：共 '+this.fmt(sum)+' 条；'+this.freshness(d);
      }catch(e){this.warn('接入趋势加载失败',e)}
      finally{this.trendBusy=false}
    },
    // 实时接入趋势：Linux/Windows 双序列堆叠面积图
    drawOverviewTrend(){
      const d=this.trend||{},pts=d.points||[];
      const labels=pts.map(p=>this.fmtClock(p.bucket,'HM'));
      const empty=!pts.length||pts.every(p=>!Number(p.total));
      const option={
        animationDuration:400,animationDurationUpdate:280,animationEasing:'cubicOut',animationEasingUpdate:'cubicOut',
        color:['#2f6bff','#4cc38a'],
        tooltip:this.tooltipAxis(),
        legend:{data:['Linux','Windows'],right:6,top:0,icon:'roundRect',itemWidth:10,itemHeight:6,textStyle:{fontSize:11,color:'#5f6e85'}},
        title:empty?this.emptyTitle('暂无数据'):{show:false},
        grid:{left:6,right:34,top:34,bottom:2,containLabel:true},
        xAxis:{type:'category',boundaryGap:false,data:labels,axisTick:{show:false},axisLine:this.axisLine(),
          axisLabel:{fontSize:11,color:'#728197',interval:Math.max(0,Math.ceil(labels.length/8)-1)}},
        yAxis:{type:'value',splitLine:this.splitLine(),axisLine:{show:false},
          axisLabel:{fontSize:11,color:'#728197',formatter:v=>v>=10000?(v/10000)+'万':v}},
        series:[
          {name:'Linux',type:'line',stack:'接入量',smooth:true,showSymbol:false,lineStyle:{width:2},
            areaStyle:{color:this.areaGradient('#2f6bff')},data:pts.map(p=>Number(p.linux||0))},
          {name:'Windows',type:'line',stack:'接入量',smooth:true,showSymbol:false,lineStyle:{width:2},
            areaStyle:{color:this.areaGradient('#4cc38a')},data:pts.map(p=>Number(p.windows||0))}
        ]
      };
      this.paint('trend-chart',option);
    },
    // 平台占比半环仪表：中心数字是总量，弧上是两平台百分比
    drawPlatformGauge(){
      const bp=(this.summary&&this.summary.by_platform)||{};
      const linux=Number(bp.Linux||0),win=Number(bp.Windows||0),total=linux+win;
      const option={
        animationDuration:400,animationDurationUpdate:280,animationEasing:'cubicOut',animationEasingUpdate:'cubicOut',
        color:['#2f6bff','#4cc38a'],
        tooltip:{trigger:'item',formatter:p=>p.marker+p.name+'<br/>'+this.fmt(p.value)+' 条（'+p.percent.toFixed(1)+'%）'},
        legend:{bottom:0,left:'center',icon:'circle',itemWidth:9,itemHeight:9,textStyle:{fontSize:11,color:'#5f6e85'}},
        title:total?{show:false}:this.emptyTitle('暂无数据','52%'),
        series:[{
          type:'pie',radius:['62%','82%'],center:['50%','52%'],startAngle:180,endAngle:0,minAngle:2,
          itemStyle:{borderColor:'#fff',borderWidth:2},
          // 空窗口时两个 0 值切片没有可见扇形，但 ECharts 仍会画「0.0%」标签并甩到画布角落
          label:{show:total>0,position:'inside',formatter:p=>p.percent.toFixed(1)+'%',fontSize:11,color:'#fff'},
          labelLine:{show:false},
          data:[{name:'Linux',value:linux},{name:'Windows',value:win}]
        },this.centerPie(this.fmt(total),'日志总量',['50%','44%'],22,total>0)]
      };
      this.paint('platform-gauge',option);
    },
    // 概览环形图。三个必须守住的点：① 复用同一 ECharts 实例（原先每次刷新都 echarts.init 一遍，
    // 反复新建实例并打印 "instance already initialized" 告警）；② 区块被 x-show 隐藏时容器宽高为 0，
    // 必须直接跳过，否则会画成 0×0；③ 图例与环形图各占一块区域，避免长类别名压住图形。
    drawChart(){
      const all=this.summary.categories||[];
      // 0 条类别在环形图上是不可见的切片，只会在图例里堆字，直接省略并在图下注明
      const rows=all.filter(x=>Number(x.count)>0).map(x=>({name:x.platform+' · '+x.label,value:Number(x.count)}));
      const zero=all.length-rows.length;
      this.chartNote=rows.length?(zero?`另有 ${zero} 个类别当前为 0 条，已从环形图省略`:''):'尚未采集到任何日志';
      const width=this.chartWidth('category-chart');
      if(!width)return;
      const trunc=(w)=>({fontSize:11,color:'#5f6e85',width:w,overflow:'truncate'});
      const option={
        animationDuration:400,animationDurationUpdate:280,animationEasing:'cubicOut',animationEasingUpdate:'cubicOut',
        color:['#2f6bff','#4cc38a','#f2b134','#f2705b','#8f7dff','#22b8a6','#7c8db5','#c86bd6','#3aa8d8','#e0719a','#9aa8bd','#b9c3d3'],
        tooltip:{trigger:'item',formatter:p=>`${p.marker}${p.name}<br/>${Number(p.value).toLocaleString('zh-CN')} 条（${p.percent.toFixed(1)}%）`},
        legend:{type:'scroll',icon:'circle',itemWidth:10,itemHeight:10,
          pageIconColor:'#2867d8',pageIconInactiveColor:'#c2ccdb',pageTextStyle:{color:'#5f6e85',fontSize:11},
          tooltip:{show:true}},
        title:rows.length?{show:false}:this.emptyTitle('暂无日志数据'),
        series:[{type:'pie',avoidLabelOverlap:true,minAngle:2,minShowLabelAngle:5,
          itemStyle:{borderColor:'#fff',borderWidth:2},
          label:{show:true,fontSize:11,color:'#61708a',formatter:p=>p.percent.toFixed(1)+'%'},
          labelLine:{show:true,length:8,length2:8,lineStyle:{color:'#cfd9e8'}},
          emphasis:{scale:true,scaleSize:6,label:{fontWeight:'bold'}},
          data:rows}]
      };
      if(width<560){
        // 窄屏：图例换到下方并允许折行（scroll 型在窄屏会分页成「1/3」并把条目顶出画布），
        // 环形图上移并缩小，同时关掉外侧文字标签——窄屏画标签必然压住图例
        Object.assign(option.legend,{type:'plain',orient:'horizontal',left:'center',right:'auto',top:'auto',bottom:0,itemGap:8,textStyle:trunc(100)});
        Object.assign(option.series[0],{center:['50%','38%'],radius:['30%','50%'],label:{show:false},labelLine:{show:false}});
      }else{
        // 宽屏：图例独占右侧一列，环形图左移让位，两者互不遮挡
        Object.assign(option.legend,{orient:'vertical',left:'auto',right:6,top:'middle',bottom:'auto',itemGap:9,textStyle:trunc(190)});
        Object.assign(option.series[0],{center:['32%','50%'],radius:['46%','70%']});
      }
      this.paint('category-chart',option);
    },

    /* ===== 客户端心跳监控 ================================================= */
    clientStatus(c){return c.heartbeatStatus||c.status||'OFFLINE'},
    // WebSocket 快照（字段是 c.client_id/c.status）与 REST 降级（camelCase）都汇到这一条路径
    applyCounts(total,online,delayed,offline){
      this.hbCounts={total:Number(total||0),online:Number(online||0),delayed:Number(delayed||0),offline:Number(offline||0)};
      const rate=total?Math.round(online/total*100):0;
      this.setMetrics('heartbeatMetrics',[
        {label:'客户端总数',value:this.fmt(total),num:Number(total||0),hint:'纳管客户端'},
        {label:'在线客户端',value:this.fmt(online),num:Number(online||0),hint:rate+'% 在线率'},
        {label:'延迟客户端',value:this.fmt(delayed),num:Number(delayed||0),hint:'需要关注'},
        {label:'离线客户端',value:this.fmt(offline),num:Number(offline||0),hint:'连接中断'}
      ]);
      this.drawHeartbeatCharts();
    },
    drawHeartbeatCharts(){this.drawStatusDonut()},
    async loadHeartbeats(){
      try{
        const [s,d]=await Promise.all([this.api('/api/v1/monitor/summary'),this.api('/api/v1/monitor/clients')]);
        this.clients=d.items||[];
        this.applyCounts(s.total,s.states.ONLINE,s.states.DELAYED,s.states.OFFLINE);
      }catch(e){this.warn('心跳数据加载失败',e)}
    },
    // 心跳历史（15 分钟滚动窗口）：延迟趋势图的数据源，10 秒轮询（服务端不查库）
    async loadHistory(){
      if(this.historyBusy)return;
      if(this.page!=='heartbeat')return;
      this.historyBusy=true;
      try{
        const d=await this.api('/api/v1/monitor/history?minutes=15');
        this.history=d;
        this.drawDelayChart();
        const all=d.clients||[];
        // 掉线的机器不再展示延迟：离线时「延迟」= 心跳年龄，只会一路涨到无意义的大值
        const live=all.filter(c=>this.clientStatus(c)!=='OFFLINE');
        const excluded=all.length-live.length;
        const shown=Math.min(live.length,8);
        const extra=live.length-shown;
        this.historyNote=!d.samples
          ?'暂无历史采样点：该序列存在进程内存里，服务重启后会清零，每 5 秒采样一次，稍后自动出现曲线'
          :('最近 '+d.windowMinutes+' 分钟共 '+d.samples+' 个采样点（每 '+d.intervalSec+' 秒一个）；按原始采样值绘制折线（未做平滑），纵轴按峰值取整档（不随小波动缩放），平均延迟按图中曲线计算'
            +(excluded>0?'；已排除 '+excluded+' 个离线客户端（离线不展示心跳延迟）':'')
            +(extra>0?'；曲线最多显示 '+shown+' 个客户端，另有 '+extra+' 个未显示':''));
      }catch(e){this.warn('心跳历史加载失败',e)}
      finally{this.historyBusy=false}
    },
    // 延迟趋势：视觉与概览「接入趋势」同一套——主序列是带渐变的面积曲线，客户端细线退成背景线索。
    //
    // 曲线按**原始采样值**直接绘制折线（`smooth:false`，不做 EWMA、不做滑动平均）：
    // 2026-10-13 按需求「取消平滑效果，直接上折线的效果」改回原样。平滑会掩盖单点尖峰，
    // 而心跳延迟的尖峰正是要看的东西；如果以后又觉得晃，再在绘制前接一层平滑即可（git 历史里有）。
    // 唯一保留的「稳定观感」措施是纵轴锚定：`niceDelayMax()` 把上限取成整数档并留 25% 余量，
    // 避免自动缩放把 3~4 秒之间的小波动撑满整个高度。
    // null 表示该时刻没有采样（断点不连）。
    // 延迟纵轴上限：取 5/10/15/20/30/45/60… 的「整数档」，并留 25% 余量；
    // 下限 5 秒——延迟在 1 秒上下轻微波动时，若轴只有 2 秒高，0.2 秒的起伏也会占满 10% 的高度。
    niceDelayMax(max){
      const steps=[5,10,15,20,30,45,60,90,120,180,300];
      const target=Math.max(Number(max||0)*1.25,this.DELAY_AXIS_FLOOR);
      for(const step of steps)if(target<=step)return step;
      return Math.ceil(target/60)*60;
    },
    drawDelayChart(){
      const d=this.history||{},pts=d.points||[];
      const labels=pts.map(p=>this.fmtClock(p.t,'HMS'));
      const index={};pts.forEach((p,i)=>{index[p.t]=i});
      const palette=['#4cc38a','#f2b134','#8f7dff','#22b8a6','#c86bd6','#3aa8d8','#e0719a','#7c8db5'];
      // 离线客户端（心跳年龄 > 30 秒）不进入延迟图：那不是「延迟大」，而是已经掉线
      const live=(d.clients||[]).filter(c=>this.clientStatus(c)!=='OFFLINE');
      const lines=live.slice(0,8).map((cl,i)=>{
        const line=new Array(labels.length).fill(null);
        (cl.points||[]).forEach(([t,v])=>{const k=index[t];if(k!=null)line[k]=Number(v)});
        return {name:cl.clientId,type:'line',smooth:false,showSymbol:false,connectNulls:false,z:2,
          lineStyle:{width:1,opacity:0.35},itemStyle:{color:palette[i%palette.length]},
          emphasis:{lineStyle:{width:2,opacity:1}},data:line};
      });
      // 平均线只对「画出来的这些客户端」求平均：直接用接口的 avgDelay 会把离线客户端的陈旧延迟算进去，
      // 均值会浮在所有曲线之上，与图里看到的完全对不上。平均值同样不做平滑。
      const average=labels.map((_,i)=>{
        const values=lines.map(l=>l.data[i]).filter(v=>typeof v==='number');
        return values.length?Math.round(values.reduce((a,b)=>a+b,0)/values.length*100)/100:null;
      });
      const empty=!labels.length||!lines.length;
      const peak=average.reduce((a,v)=>typeof v==='number'&&v>a?v:a,0);
      const axisMax=this.niceDelayMax(peak);
      // 心跳状态阈值是 10 秒（在线/延迟分界）：纵轴够得着时才画这条参考线，
      // 小延迟场景（轴上限 < 10 秒）画上去只会把曲线压扁
      const guide=axisMax>=10?[{yAxis:10,silent:true,lineStyle:{color:'#f2b134',type:'dashed',width:1},
        label:{show:true,formatter:'延迟阈值 10s',position:'insideEndBottom',fontSize:10,color:'#b7791f'}}]:[];
      const option={
        animationDuration:400,animationDurationUpdate:280,animationEasing:'cubicOut',animationEasingUpdate:'cubicOut',
        color:['#2f6bff'].concat(palette),
        tooltip:this.tooltipAxis('秒'),
        legend:{data:['平均延迟'].concat(lines.map(l=>l.name)),type:'scroll',right:6,top:0,icon:'roundRect',itemWidth:10,itemHeight:6,
          pageIconColor:'#2867d8',pageIconInactiveColor:'#c2ccdb',pageTextStyle:{color:'#5f6e85',fontSize:11},textStyle:{fontSize:11,color:'#5f6e85'}},
        title:empty?this.emptyTitle(labels.length?'暂无未掉线客户端':'暂无历史采样点'):{show:false},
        grid:{left:6,right:34,top:34,bottom:2,containLabel:true},
        xAxis:{type:'category',boundaryGap:false,data:labels,axisTick:{show:false},axisLine:this.axisLine(),
          axisLabel:{fontSize:11,color:'#728197',interval:Math.max(0,Math.ceil(labels.length/8)-1)}},
        yAxis:{type:'value',min:0,max:axisMax,splitNumber:4,name:'秒',nameTextStyle:{fontSize:11,color:'#8b98ad'},
          splitLine:this.splitLine(),axisLine:{show:false},axisLabel:{fontSize:11,color:'#728197'}},
        series:[{name:'平均延迟',type:'line',smooth:false,showSymbol:false,z:5,connectNulls:true,
          // 线宽 2：比客户端细线（1px）略粗一档，保持主视线但不抢戏；2026-10-13 按需求从 3px 收细
          lineStyle:{width:2,shadowColor:'rgba(47,107,255,.30)',shadowBlur:6,shadowOffsetY:2},
          itemStyle:{color:'#2f6bff'},
          areaStyle:{color:this.areaGradient('#2f6bff')},
          markLine:{silent:true,symbol:'none',data:guide},
          data:average}].concat(lines)
      };
      this.paint('delay-chart',option);
    },
    // 状态分布甜甜圈：ONLINE / DELAYED / OFFLINE（状态本身仍要完整展示，只是不参与延迟图）
    drawStatusDonut(){
      const {total,online,delayed,offline}=this.hbCounts;
      const rows=[
        {name:'在线',value:online,itemStyle:{color:'#4cc38a'}},
        {name:'延迟',value:delayed,itemStyle:{color:'#f2b134'}},
        {name:'离线',value:offline,itemStyle:{color:'#f2705b'}}
      ];
      const option={
        animationDuration:400,animationDurationUpdate:280,animationEasing:'cubicOut',animationEasingUpdate:'cubicOut',
        tooltip:{trigger:'item',formatter:p=>p.marker+p.name+'<br/>'+this.fmt(p.value)+' 个（'+p.percent.toFixed(1)+'%）'},
        legend:{bottom:0,left:'center',icon:'circle',itemWidth:9,itemHeight:9,textStyle:{fontSize:11,color:'#5f6e85'}},
        title:total?{show:false}:this.emptyTitle('暂无心跳数据'),
        series:[{
          type:'pie',radius:['52%','72%'],center:['50%','44%'],minAngle:2,
          itemStyle:{borderColor:'#fff',borderWidth:2},
          label:{show:total>0,position:'inside',formatter:p=>p.value?p.value:'',fontSize:11,color:'#fff'},
          labelLine:{show:false},
          data:rows
        },this.centerPie(this.fmt(total),'客户端',['50%','44%'],20,total>0)]
      };
      this.paint('status-donut',option);
    },

    /* ===== 融合日志 ======================================================= */
    async loadLogs(){try{if(!this.categories.length)this.categories=await this.api('/api/v1/logs/categories');const choices=this.categories.filter(x=>x.platform===this.logPlatform);if(!choices.some(x=>x.key===this.logCategory))this.logCategory=choices[0]?.key||'';const d=await this.api(`/api/v1/logs/categories/${this.logCategory}?platform=${this.logPlatform}&page=${this.logPage}&size=${this.PAGE_SIZE}&keyword=${encodeURIComponent(this.keyword)}`);this.logs=d.items||[];this.logTotal=Number(d.total||0);this.logPages=Math.max(1,Math.ceil(this.logTotal/this.PAGE_SIZE));this.logKeys=this.logs.length?Object.keys(this.logs[0]):[]}catch(e){this.warn('日志列表加载失败',e)}},
    // 切换平台/类别/关键词时回到第 1 页，否则会停在上一个查询的页码上
    searchLogs(){this.logPage=1;return this.loadLogs()},
    gotoLogPage(p){if(p<1||p>this.logPages||p===this.logPage)return;this.logPage=p;return this.loadLogs()},

    /* ===== 日志统计图表（子页）============================================ */
    statsBucket(){return this.statsRange==='30d'?'day':'hour'},
    statsPlatformQuery(){return this.statsPlatform?('&platform='+this.statsPlatform):''},
    async loadLogStats(force){
      if(this.statsBusy)return;
      if(!force&&this.page!=='log-stats')return;
      this.statsBusy=true;
      const platform=this.statsPlatformQuery(),bucket=this.statsBucket();
      try{
        // 三份数据一次并发取回：趋势随筛选器、热力图固定 7 天小时桶（前端按本地时区聚合 7×24）、排行随筛选器
        const [trend,heat,rank]=await Promise.all([
          this.api(`/api/v1/logs/trend?range=${this.statsRange}&bucket=${bucket}${platform}`),
          this.api(`/api/v1/logs/trend?range=7d&bucket=hour${platform}`),
          this.api(`/api/v1/logs/stats?range=${this.statsRange}&limit=10${platform}`)
        ]);
        this.statsTrend=trend;this.statsHeat=heat;this.statsRank=rank;
        this.drawStatsAll();
        const pts=trend.points||[];
        const sum=pts.reduce((a,p)=>a+Number(p.total||0),0);
        this.statsTrendNote='窗口内共 '+this.fmt(sum)+' 条，按 '+(bucket==='day'?'天':'小时')+' 桶；'+this.freshness(trend);
        this.statsNote='TOP '+(rank.topCategories||[]).length+' 个类别、TOP '+(rank.topHosts||[]).length+' 台主机（窗口内计数，与全表口径的「日志总量」不同）；'+this.freshness(rank);
      }catch(e){this.warn('日志统计加载失败',e)}
      finally{this.statsBusy=false}
    },
    drawStatsAll(){this.drawStatsTrend();this.drawStatsPlatform();this.drawStatsHeatmap();this.drawStatsHosts();this.drawStatsCategories()},
    // 统计页趋势：平台筛选决定单/双序列，24h/7d 用小时桶、30d 用天桶
    drawStatsTrend(){
      const d=this.statsTrend||{},pts=d.points||[],bucket=d.bucket||this.statsBucket();
      const labels=pts.map(p=>this.axisLabelOf(p.bucket,bucket));
      const both=!this.statsPlatform;
      const empty=!pts.length||pts.every(p=>!Number(p.total));
      const series=both?[
        {name:'Linux',type:'line',stack:'接入量',smooth:true,showSymbol:false,lineStyle:{width:2},areaStyle:{color:this.areaGradient('#2f6bff')},data:pts.map(p=>Number(p.linux||0))},
        {name:'Windows',type:'line',stack:'接入量',smooth:true,showSymbol:false,lineStyle:{width:2},areaStyle:{color:this.areaGradient('#4cc38a')},data:pts.map(p=>Number(p.windows||0))}
      ]:[
        {name:this.statsPlatform,type:'line',smooth:true,showSymbol:false,lineStyle:{width:2},itemStyle:{color:this.statsPlatform==='Linux'?'#2f6bff':'#4cc38a'},
          areaStyle:{color:this.areaGradient(this.statsPlatform==='Linux'?'#2f6bff':'#4cc38a')},
          data:pts.map(p=>Number(this.statsPlatform==='Linux'?p.linux:p.windows))}
      ];
      const option={
        animationDuration:400,animationDurationUpdate:280,animationEasing:'cubicOut',animationEasingUpdate:'cubicOut',
        color:['#2f6bff','#4cc38a'],
        tooltip:this.tooltipAxis(),
        legend:{data:both?['Linux','Windows']:[this.statsPlatform],right:6,top:0,icon:'roundRect',itemWidth:10,itemHeight:6,textStyle:{fontSize:11,color:'#5f6e85'}},
        title:empty?this.emptyTitle('暂无数据'):{show:false},
        grid:{left:6,right:34,top:34,bottom:2,containLabel:true},
        xAxis:{type:'category',boundaryGap:false,data:labels,axisTick:{show:false},axisLine:this.axisLine(),
          axisLabel:{fontSize:11,color:'#728197',interval:Math.max(0,Math.ceil(labels.length/8)-1)}},
        yAxis:{type:'value',splitLine:this.splitLine(),axisLine:{show:false},axisLabel:{fontSize:11,color:'#728197',formatter:v=>v>=10000?(v/10000)+'万':v}},
        series:series
      };
      this.paint('stats-trend-chart',option);
    },
    // 统计页平台占比甜甜圈：数据取同一窗口的合计，保证与趋势图口径一致
    drawStatsPlatform(){
      const pts=(this.statsTrend&&this.statsTrend.points)||[];
      const linux=pts.reduce((a,p)=>a+Number(p.linux||0),0),win=pts.reduce((a,p)=>a+Number(p.windows||0),0);
      const total=linux+win;
      const rows=this.statsPlatform==='Linux'?[{name:'Linux',value:linux,itemStyle:{color:'#2f6bff'}}]
        :this.statsPlatform==='Windows'?[{name:'Windows',value:win,itemStyle:{color:'#4cc38a'}}]
        :[{name:'Linux',value:linux,itemStyle:{color:'#2f6bff'}},{name:'Windows',value:win,itemStyle:{color:'#4cc38a'}}];
      const option={
        animationDuration:400,animationDurationUpdate:280,animationEasing:'cubicOut',animationEasingUpdate:'cubicOut',
        tooltip:{trigger:'item',formatter:p=>p.marker+p.name+'<br/>'+this.fmt(p.value)+' 条（'+p.percent.toFixed(1)+'%）'},
        legend:{bottom:0,left:'center',icon:'circle',itemWidth:9,itemHeight:9,textStyle:{fontSize:11,color:'#5f6e85'}},
        title:total?{show:false}:this.emptyTitle('暂无数据'),
        series:[{type:'pie',radius:['56%','76%'],center:['50%','46%'],minAngle:2,itemStyle:{borderColor:'#fff',borderWidth:2},
          label:{show:total>0,position:'inside',formatter:p=>p.percent.toFixed(1)+'%',fontSize:11,color:'#fff'},labelLine:{show:false},data:rows},
          this.centerPie(this.fmt(total),'窗口内合计',['50%','46%'],20,total>0)]
      };
      this.paint('stats-platform-chart',option);
    },
    // 24 小时 × 星期热力图：后端只出 UTC 小时桶，7×24 矩阵在前端按本地时区聚合
    buildHeatmap(points){
      const days=['周一','周二','周三','周四','周五','周六','周日'];
      const hours=[];for(let h=0;h<24;h++)hours.push(this.pad(h));
      const grid={};let max=0;
      (points||[]).forEach(p=>{
        const d=new Date(p.bucket);if(isNaN(d.getTime()))return;
        const row=(d.getDay()+6)%7,hour=d.getHours();
        const key=row*24+hour,v=Number(p.total||0);
        grid[key]=(grid[key]||0)+v;
        if(grid[key]>max)max=grid[key];
      });
      const data=[];
      for(let r=0;r<7;r++)for(let h=0;h<24;h++)data.push([h,r,grid[r*24+h]||0]);
      return {days:days,hours:hours,data:data,max:max};
    },
    drawStatsHeatmap(){
      const heat=this.buildHeatmap((this.statsHeat&&this.statsHeat.points)||[]);
      const option={
        animationDuration:400,animationDurationUpdate:280,
        tooltip:{position:'top',formatter:p=>heat.days[p.value[1]]+' '+heat.hours[p.value[0]]+':00<br/>'+this.fmt(p.value[2])+' 条'},
        grid:{left:6,right:12,top:8,bottom:52,containLabel:true},
        // 空窗口时 168 个格子全是 0，热力图本身就是一片浅色底：必须显式给出空态文字
        title:heat.max?{show:false}:this.emptyTitle('暂无数据'),
        xAxis:{type:'category',data:heat.hours,splitArea:{show:true},axisTick:{show:false},axisLine:this.axisLine(),axisLabel:{fontSize:11,color:'#728197'}},
        yAxis:{type:'category',data:heat.days,splitArea:{show:true},axisTick:{show:false},axisLine:this.axisLine(),axisLabel:{fontSize:11,color:'#728197'}},
        visualMap:{min:0,max:heat.max||1,calculable:true,orient:'horizontal',left:'center',bottom:0,
          itemWidth:12,itemHeight:90,textStyle:{fontSize:11,color:'#728197'},
          inRange:{color:['#e8f1ff','#bcd3ff','#7ea8f5','#3f6fd8','#142d70']}},
        series:[{type:'heatmap',data:heat.data,label:{show:false},
          itemStyle:{borderColor:'#fff',borderWidth:1},
          emphasis:{itemStyle:{shadowBlur:6,shadowColor:'rgba(20,45,112,.35)'}}}]
      };
      this.paint('stats-heatmap',option);
    },
    // TOP 主机排行（横向条形图，最大值在顶部）
    drawStatsHosts(){
      const rows=(((this.statsRank||{}).topHosts)||[]).slice(0,10).slice().reverse();
      const option={
        animationDuration:400,animationDurationUpdate:280,animationEasing:'cubicOut',animationEasingUpdate:'cubicOut',
        tooltip:{trigger:'item',formatter:p=>p.name+'<br/>'+this.fmt(p.value)+' 条'},
        title:rows.length?{show:false}:this.emptyTitle('暂无数据'),
        grid:{left:6,right:56,top:8,bottom:2,containLabel:true},
        xAxis:{type:'value',splitLine:this.splitLine(),axisLine:{show:false},axisLabel:{fontSize:11,color:'#728197',formatter:v=>v>=10000?(v/10000)+'万':v}},
        yAxis:{type:'category',data:rows.map(r=>r.host),axisTick:{show:false},axisLine:this.axisLine(),axisLabel:{fontSize:11,color:'#5f6e85',width:110,overflow:'truncate'}},
        series:[{type:'bar',barMaxWidth:14,itemStyle:{borderRadius:[0,4,4,0],color:'#7ea8f5'},
          label:{show:true,position:'right',fontSize:11,color:'#728197',formatter:p=>this.fmt(p.value)},data:rows.map(r=>Number(r.count||0))}]
      };
      this.paint('stats-hosts-chart',option);
    },
    // TOP 日志类别排行：按平台着色，Linux 蓝 / Windows 绿
    drawStatsCategories(){
      const items=(((this.statsRank||{}).topCategories)||[]).slice(0,12);
      const rows=items.slice().reverse();
      const option={
        animationDuration:400,animationDurationUpdate:280,animationEasing:'cubicOut',animationEasingUpdate:'cubicOut',
        tooltip:{trigger:'item',formatter:p=>{const it=rows[p.dataIndex]||{};return (it.platform||'')+' · '+(it.label||p.name)+'<br/>'+this.fmt(p.value)+' 条'}},
        title:rows.length?{show:false}:this.emptyTitle('暂无数据'),
        grid:{left:6,right:64,top:8,bottom:2,containLabel:true},
        xAxis:{type:'value',splitLine:this.splitLine(),axisLine:{show:false},axisLabel:{fontSize:11,color:'#728197',formatter:v=>v>=10000?(v/10000)+'万':v}},
        yAxis:{type:'category',data:rows.map(r=>r.platform+' · '+r.label),axisTick:{show:false},axisLine:this.axisLine(),axisLabel:{fontSize:11,color:'#5f6e85',width:160,overflow:'truncate'}},
        series:[{type:'bar',barMaxWidth:14,
          itemStyle:{borderRadius:[0,4,4,0],color:p=>((rows[p.dataIndex]||{}).platform==='Linux'?'#2f6bff':'#4cc38a')},
          label:{show:true,position:'right',fontSize:11,color:'#728197',formatter:p=>this.fmt(p.value)},
          data:rows.map(r=>Number(r.count||0))}]
      };
      this.paint('stats-categories-chart',option);
    },

    /* ===== 完整性存证 ===================================================== */
    // 逐条校验由后端在 SQL 里复算 sha256(log_timestamp + 0x1F + raw_log) 得到
    // verify_status，前端只负责展示：HASH_MISMATCH 表示原文/时间戳/摘要被改动。
    verifyLabel(s){return s==='VALID'?'一致':s==='HASH_MISMATCH'?'哈希不一致':'原文缺失'},
    verifyBadgeClass(s){return s==='VALID'?'text-bg-success':s==='HASH_MISMATCH'?'text-bg-danger':'text-bg-warning'},
    async loadEvidence(){
      try{
        const q=new URLSearchParams({page:this.evidencePage,size:this.PAGE_SIZE});
        if(this.evidenceVerify)q.set('verify',this.evidenceVerify);
        if(this.evidenceKeyword)q.set('keyword',this.evidenceKeyword);
        if(this.evidencePlatform)q.set('platform',this.evidencePlatform);
        if(this.evidenceStart)q.set('start',this.evidenceStart);
        if(this.evidenceEnd)q.set('end',this.evidenceEnd);
        const [s,d]=await Promise.all([
          this.api('/api/v1/evidence/summary'),
          this.api('/api/v1/evidence?'+q.toString()),
        ]);
        const st=s.statuses||{};
        this.evidence=d.items||[];
        this.evidenceTotal=Number(d.total||0);
        this.evidencePages=Math.max(1,Number(d.pages||Math.ceil(this.evidenceTotal/this.PAGE_SIZE)));
        this.evidenceSource=(d.source?s.source.host+' / '+s.source.database+'.'+s.source.table:'');
        this.evidenceTampered=Number(st.HASH_MISMATCH||0)+Number(st.RAW_MISSING||0);
        this.setMetrics('evidenceMetrics',[
          {label:'存证总数',value:this.fmt(s.total),num:Number(s.total||0),hint:'原始日志记录'},
          {label:'校验一致',value:this.fmt(st.VALID),num:Number(st.VALID||0),hint:'复算摘要与存证一致',tone:'ok'},
          {label:'哈希不一致',value:this.fmt(st.HASH_MISMATCH),num:Number(st.HASH_MISMATCH||0),hint:'原文或时间戳被改动',tone:'bad'},
          {label:'原文缺失',value:this.fmt(st.RAW_MISSING),num:Number(st.RAW_MISSING||0),hint:'无从复算',tone:'warn'},
        ]);
      }catch(e){this.warn('存证数据加载失败',e)}
    },
    gotoEvidencePage(p){if(p<1||p>this.evidencePages||p===this.evidencePage)return;this.evidencePage=p;return this.loadEvidence()},
    // 导出当前筛选条件：与列表使用同一组参数，导出的 CSV 与页面看到的完全一致
    evidenceExportUrl(){
      const q=new URLSearchParams();
      if(this.evidenceVerify)q.set('verify',this.evidenceVerify);
      if(this.evidenceKeyword)q.set('keyword',this.evidenceKeyword);
      if(this.evidencePlatform)q.set('platform',this.evidencePlatform);
      if(this.evidenceStart)q.set('start',this.evidenceStart);
      if(this.evidenceEnd)q.set('end',this.evidenceEnd);
      const suffix=q.toString();
      return '/api/v1/evidence/export'+(suffix?'?'+suffix:'');
    },
    exportEvidence(){location.href=this.evidenceExportUrl()},
    resetEvidenceFilters(){
      this.evidenceVerify='';this.evidenceKeyword='';this.evidencePlatform='';
      this.evidenceStart='';this.evidenceEnd='';this.evidencePage=1;return this.loadEvidence();
    },
    // 存证中心分区：只有一个页面，但按职责拆分，避免归档等危险操作与日常查询混在一起
    evidenceTabs(){return [
      {key:'overview',label:'存证总览'},
      {key:'ledger',label:'存证台账'},
      {key:'archive',label:'归档管理'},
      {key:'policy',label:'算法与策略'},
    ]},
    gotoEvidenceTab(k){this.evidenceTab=k},

    // 归档：先「仅统计」看到会删多少条，再二次确认执行导出并删除
    async previewArchive(){
      if(!this.archiveStart&&!this.archiveEnd){this.archiveError=true;this.archiveMessage='请至少选择开始或结束日期';return}
      this.archiveBusy=true;this.archiveError=false;this.archiveMessage='正在统计…';
      try{
        const q=new URLSearchParams();
        if(this.archiveStart)q.set('start',this.archiveStart);
        if(this.archiveEnd)q.set('end',this.archiveEnd);
        const d=await this.api('/api/v1/archive/preview?'+q.toString());
        this.archiveMessage=`该区间共有 ${this.fmt(d.candidates)} 条存证可归档（导出为 CSV 后将从数据库删除）`;
      }catch(e){this.archiveError=true;this.archiveMessage='统计失败：'+(e&&e.message?e.message:e)}
      finally{this.archiveBusy=false}
    },
    async runArchive(){
      if(!this.archiveStart&&!this.archiveEnd){this.archiveError=true;this.archiveMessage='请至少选择开始或结束日期';return}
      const range=(this.archiveStart||'最早')+' 至 '+(this.archiveEnd||'现在');
      if(!window.confirm(`将导出并【永久删除】${range} 的存证记录。\n\n导出文件确认落盘后才会删除，删除不可恢复。确定继续？`))return;
      this.archiveBusy=true;this.archiveError=false;this.archiveMessage='正在导出并删除…';
      try{
        const r=await fetch('/api/v1/archive/manual',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start:this.archiveStart,end:this.archiveEnd,confirm:true})});
        const j=await r.json();
        if(!r.ok){this.archiveError=true;this.archiveMessage='归档失败：'+(j.detail||('HTTP '+r.status));return}
        const d=j.data||j;
        this.archiveMessage=`归档完成：导出 ${this.fmt(d.exported)} 条并删除 ${this.fmt(d.deleted)} 条，文件 ${d.file||'-'}`;
        this.evidencePage=1;await this.loadEvidence();
      }catch(e){this.archiveError=true;this.archiveMessage='归档失败：'+(e&&e.message?e.message:e)}
      finally{this.archiveBusy=false}
    },
    async runAutoArchive(){
      const label=this.archiveMonths+' 个日历月之前（按入库时间）';
      if(!window.confirm(`将导出并【永久删除】${label}的全部存证记录。\n\n导出文件确认落盘后才会删除，删除不可恢复。确定继续？`))return;
      this.archiveBusy=true;this.archiveError=false;this.archiveMessage='正在执行自动归档…';
      try{
        const r=await fetch('/api/v1/archive/auto',{method:'POST'});
        const j=await r.json();
        if(!r.ok){this.archiveError=true;this.archiveMessage='自动归档失败：'+(j.detail||('HTTP '+r.status));return}
        const d=j.data||j;
        if(d.enabled===false){this.archiveMessage='自动归档未启用（archive_retention_months <= 0）';return}
        this.archiveMessage=d.exported?`自动归档完成：导出 ${this.fmt(d.exported)} 条并删除 ${this.fmt(d.deleted)} 条，文件 ${d.file}`:`没有超过 ${this.archiveMonths} 个月的存证需要归档（截止 ${d.cutoff}）`;
        if(d.deleted){this.evidencePage=1;await this.loadEvidence()}
      }catch(e){this.archiveError=true;this.archiveMessage='自动归档失败：'+(e&&e.message?e.message:e)}
      finally{this.archiveBusy=false}
    },
    /* ===== 系统维护：触发服务端代码更新 =====================================
     * 接口只负责起一个独立的 systemd 单元后立即返回；服务随后会被重启，
     * 因此这里主动轮询 /health，恢复后自动刷新页面。 */
    async runUpdate(){
      if(!window.confirm('将从 GitHub 拉取最新代码并重启后端服务。\n\n更新期间服务会短暂中断（约 10–30 秒），页面会自动等待并刷新。确定继续？'))return;
      this.updateBusy=true;this.updateError=false;this.updateMessage='正在触发更新…';
      try{
        const r=await fetch('/api/v1/admin/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});
        const j=await r.json().catch(()=>({}));
        if(!r.ok){this.updateError=true;this.updateMessage='触发更新失败：'+((j&&j.detail)||('HTTP '+r.status));return}
        const note=(j.data&&j.data.note)||'';
        this.updateMessage='更新已启动，服务即将重启…'+(note?'（'+note+'）':'');
        this.pollUpdateRecovery();
      }catch(e){this.updateError=true;this.updateMessage='触发更新失败：'+(e&&e.message?e.message:e)}
      finally{this.updateBusy=false}
    },
    pollUpdateRecovery(){
      if(this.updatePolling)return;
      this.updatePolling=true;
      const started=Date.now();
      const tick=async()=>{
        if(Date.now()-started>180000){
          this.updatePolling=false;this.updateError=true;
          this.updateMessage='等待服务恢复超时（3 分钟）。请到服务器执行 journalctl -u log-fusion-update -f 查看日志。';
          return;
        }
        let ok=false;
        try{
          const ctl=new AbortController();const timer=setTimeout(()=>ctl.abort(),3000);
          const r=await fetch('/health',{cache:'no-store',signal:ctl.signal});
          clearTimeout(timer);ok=r.ok;
        }catch(e){ok=false}
        if(ok){
          this.updateMessage='服务已恢复，正在刷新页面…';
          this.updatePolling=false;
          setTimeout(()=>location.reload(),800);
          return;
        }
        setTimeout(tick,2000);
      };
      setTimeout(tick,5000);
    },
    exportLogs(){location.href=`/api/v1/logs/export?platform=${this.logPlatform}&category=${this.logCategory}&size=1000`},
    exportClients(){const text=['clientId,serverName,ipAddress,os,status',...this.clients.map(c=>[c.clientId,c.serverName,c.ipAddress,c.os_type,c.heartbeatStatus].map(x=>'"'+String(x||'').replaceAll('"','""')+'"').join(','))].join('\\n');const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\\ufeff'+text],{type:'text/csv'}));a.download='clients.csv';a.click()},
    connectMonitorWs(){const proto=location.protocol==='https:'?'wss':'ws';this.wsStatus='connecting';let ws;try{ws=new WebSocket(`${proto}://${location.host}/ws/client-monitor`)}catch(e){this.wsStatus='disconnected';this.syncRealtimeMetric();this.scheduleWsReconnect();return}this.ws=ws;this.wsLastMsg=Date.now();ws.onopen=()=>{this.wsStatus='connected';this.wsRetry=0;this.wsLastMsg=Date.now();this.syncRealtimeMetric()};ws.onmessage=(ev)=>{this.wsLastMsg=Date.now();try{this.applySnapshot(JSON.parse(ev.data))}catch(e){}};ws.onclose=()=>{this.wsStatus='disconnected';this.syncRealtimeMetric();this.scheduleWsReconnect()};ws.onerror=()=>{try{ws.close()}catch(e){}}},
    startWsWatchdog(){if(this.wsWatchTimer)return;this.wsWatchTimer=setInterval(()=>{const ws=this.ws;if(ws&&ws.readyState===1&&Date.now()-this.wsLastMsg>20000){try{ws.close()}catch(e){}}},5000)},
    scheduleWsReconnect(){if(this.wsTimer)return;const delay=Math.min(30000,1000*Math.pow(2,this.wsRetry++));this.wsTimer=setTimeout(()=>{this.wsTimer=null;this.connectMonitorWs()},delay)},
    // WebSocket 快照与 REST 降级都走这里：图表（在线率/分布/排行）因此自动继续更新，无额外代码路径
    applySnapshot(snap){
      if(snap.type&&snap.type!=='snapshot'){
        if(snap.type==='security_logs'&&this.page==='security')this.loadSecurity();
        return;
      }
      const clients=(snap.clients||[]).map(c=>({clientId:c.client_id,serverName:c.hostname,ipAddress:c.ip,os_type:c.system,lastHeartbeatAt:c.last_heartbeat,heartbeatDelaySec:c.heartbeatDelaySec,heartbeatStatus:c.status}));
      this.clients=clients;
      this.applyCounts(snap.total_clients,snap.online_clients,snap.delayed_clients,snap.offline_clients);
    },
    securityStatus(value){if(value==='stopped')return '已停止';if(value==='disabled')return '未安排';if(this.securitySummary?.enabled===false&&!this.securitySummary?.collectorActive&&value&&value!=='complete')return '断点已保存，等待续跑';return ({pending:'待开始',running:'处理中',complete:'已完成',watching:'持续采集中',retrying:'失败，等待重试'})[value]||'未初始化'},
    securityEpoch(value){return value?this.fmtTime(new Date(value*1000).toISOString()):'—'},
    async loadSecurity(){
      if(this.securityBusy)return;
      this.securityBusy=true;
      const kind=this.securityType,before=this.securityBefore;
      try{
        const [summary,list]=await Promise.all([
          this.api('/api/v1/security/summary'),
          this.api('/api/v1/security/logs/'+encodeURIComponent(kind)+(before?'?before='+before:''))
        ]);
        this.securitySummary=summary;
        if(this.securityType===kind&&this.securityBefore===before){this.securityRows=list.items||[];this.securityNext=list.nextBefore}
        this.securityError='';this.securityUpdated=new Date().toLocaleTimeString('zh-CN',{hour12:false});
      }catch(e){this.securityError='安全日志读取失败：'+e.message}
      finally{
        this.securityBusy=false;
        if(this.securityType!==kind||this.securityBefore!==before)this.loadSecurity();
      }
    },
    securityReset(){this.securityBefore=null;this.securityNext=null;this.securityBack=[];this.securityDetail='';this.securityRows=[];this.loadSecurity()},
    securityForward(){if(!this.securityNext||this.securityBusy)return;this.securityBack.push(this.securityBefore);this.securityBefore=this.securityNext;this.securityDetail='';this.loadSecurity()},
    securityPrevious(){if(!this.securityBack.length||this.securityBusy)return;this.securityBefore=this.securityBack.pop();this.securityDetail='';this.loadSecurity()},
    async showSecurityDetail(id){
      const kind=this.securityType;
      try{const record=await this.api('/api/v1/security/logs/'+encodeURIComponent(kind)+'/'+id);if(kind===this.securityType)this.securityDetail=JSON.stringify(record,null,2)}
      catch(e){this.securityError='日志详情读取失败：'+e.message}
    }
  }
}
