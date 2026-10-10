function dashboard() {
  return {
    page:'overview',clock:'',summary:{total:0,by_platform:{Linux:0,Windows:0},categories:[]},categories:[],clients:[],logs:[],evidence:[],logKeys:[],logPlatform:'Linux',logCategory:'authentication_session',keyword:'',heartbeatMetrics:[],evidenceMetrics:[],overviewMetrics:[],wsStatus:'connecting',ws:null,wsRetry:0,wsTimer:null,wsLastMsg:0,wsWatchTimer:null,pollTimer:null,summaryTimer:null,pageTimer:null,chart:null,chartNote:'',resizeTimer:null,
    fmt(v){return Number(v||0).toLocaleString('zh-CN')},

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

    // 只保留 WebSocket 这一条长连接：HTML/JS 之外不再占用浏览器同源连接额度
    closeStreams(){try{if(this.ws)this.ws.close()}catch(e){}},

    async init(){
      window.addEventListener('pagehide',()=>this.closeStreams());
      // 环形图默认只按容器初始尺寸绘制，窗口变化后必须重画，否则图形与图例错位
      window.addEventListener('resize',()=>{clearTimeout(this.resizeTimer);this.resizeTimer=setTimeout(()=>this.drawChart(),150)});
      this.tick();setInterval(()=>this.tick(),1000);
      await this.refreshSummary();
      try{this.categories=await this.api('/api/v1/logs/categories')}catch(e){}
      this.connectMonitorWs();this.startWsWatchdog();
      // WebSocket 断开时退化为 5 秒轮询；概览 10 秒刷新；日志/存证页各自定时刷新
      this.pollTimer=setInterval(()=>{if(this.wsStatus!=='connected')this.loadHeartbeats()},5000);
      this.summaryTimer=setInterval(()=>this.refreshSummary(),10000);
      this.pageTimer=setInterval(()=>{if(this.page==='logs')this.loadLogs();else if(this.page==='evidence')this.loadEvidence()},15000);
    },
    tick(){this.clock=new Date().toLocaleString('zh-CN',{hour12:false})},

    realtimeMetric(){
      const ok=this.wsStatus==='connected';
      return {label:'实时通道',value:ok?'正常':'降级轮询',hint:ok?'WebSocket 已连接（5 秒推送）':'WebSocket 断开，改用 5 秒轮询'};
    },
    syncRealtimeMetric(){
      if(!this.overviewMetrics||!this.overviewMetrics.length)return;
      const i=this.overviewMetrics.findIndex(m=>m.label==='实时通道');
      if(i>=0)this.overviewMetrics[i]=this.realtimeMetric();else this.overviewMetrics.push(this.realtimeMetric());
    },

    async refreshSummary(){try{this.summary=await this.api('/api/v1/logs/summary');this.overviewMetrics=[{label:'日志总量',value:this.fmt(this.summary.total),hint:'每 10 秒刷新'},{label:'Linux 日志',value:this.fmt(this.summary.by_platform.Linux),hint:'审计日志'},{label:'Windows 日志',value:this.fmt(this.summary.by_platform.Windows),hint:'事件日志'},this.realtimeMetric()];this.drawChart()}catch(e){this.warn('概览统计加载失败',e)}},
    showOverview(){this.page='overview';setTimeout(()=>this.drawChart(),0)},
    // 概览环形图。三个必须守住的点：① 复用同一 ECharts 实例（原先每次刷新都 echarts.init 一遍，
    // 反复新建实例并打印 "instance already initialized" 告警）；② 区块被 x-show 隐藏时容器宽高为 0，
    // 必须直接跳过，否则会画成 0×0；③ 图例与环形图各占一块区域，避免长类别名压住图形。
    drawChart(){
      const el=document.getElementById('category-chart');
      if(!el||!window.echarts||!el.clientWidth||!el.clientHeight)return;
      if(this.chart&&(this.chart.isDisposed()||this.chart.getDom()!==el))this.chart=null;
      if(!this.chart)this.chart=echarts.getInstanceByDom(el)||echarts.init(el);
      const all=this.summary.categories||[];
      // 0 条类别在环形图上是不可见的切片，只会在图例里堆字，直接省略并在图下注明
      const rows=all.filter(x=>Number(x.count)>0).map(x=>({name:x.platform+' · '+x.label,value:Number(x.count)}));
      const zero=all.length-rows.length;
      this.chartNote=rows.length?(zero?`另有 ${zero} 个类别当前为 0 条，已从环形图省略`:''):'尚未采集到任何日志';
      const trunc=(w)=>({fontSize:11,color:'#5f6e85',width:w,overflow:'truncate'});
      const option={
        color:['#2f6bff','#4cc38a','#f2b134','#f2705b','#8f7dff','#22b8a6','#7c8db5','#c86bd6','#3aa8d8','#e0719a','#9aa8bd','#b9c3d3'],
        tooltip:{trigger:'item',formatter:p=>`${p.marker}${p.name}<br/>${Number(p.value).toLocaleString('zh-CN')} 条（${p.percent.toFixed(1)}%）`},
        legend:{type:'scroll',icon:'circle',itemWidth:10,itemHeight:10,
          pageIconColor:'#2867d8',pageIconInactiveColor:'#c2ccdb',pageTextStyle:{color:'#5f6e85',fontSize:11},
          tooltip:{show:true}},
        title:rows.length?{show:false}:{text:'暂无日志数据',left:'center',top:'middle',textStyle:{color:'#9aa8bd',fontSize:13,fontWeight:'normal'}},
        series:[{type:'pie',avoidLabelOverlap:true,minAngle:2,minShowLabelAngle:5,
          itemStyle:{borderColor:'#fff',borderWidth:2},
          label:{show:true,fontSize:11,color:'#61708a',formatter:p=>p.percent.toFixed(1)+'%'},
          labelLine:{show:true,length:8,length2:8,lineStyle:{color:'#cfd9e8'}},
          emphasis:{scale:true,scaleSize:6,label:{fontWeight:'bold'}},
          data:rows}]
      };
      if(el.clientWidth<560){
        // 窄屏：图例换到下方并允许折行（scroll 型在窄屏会分页成「1/3」并把条目顶出画布），
        // 环形图上移并缩小，同时关掉外侧文字标签——窄屏画标签必然压住图例
        Object.assign(option.legend,{type:'plain',orient:'horizontal',left:'center',right:'auto',top:'auto',bottom:0,itemGap:8,textStyle:trunc(100)});
        Object.assign(option.series[0],{center:['50%','38%'],radius:['30%','50%'],label:{show:false},labelLine:{show:false}});
      }else{
        // 宽屏：图例独占右侧一列，环形图左移让位，两者互不遮挡
        Object.assign(option.legend,{orient:'vertical',left:'auto',right:6,top:'middle',bottom:'auto',itemGap:9,textStyle:trunc(190)});
        Object.assign(option.series[0],{center:['32%','50%'],radius:['46%','70%']});
      }
      this.chart.resize();
      this.chart.setOption(option);
    },
    async loadHeartbeats(){try{const [s,d]=await Promise.all([this.api('/api/v1/monitor/summary'),this.api('/api/v1/monitor/clients')]);this.clients=d.items||[];this.heartbeatMetrics=[{label:'客户端总数',value:s.total,hint:'纳管客户端'},{label:'在线客户端',value:s.states.ONLINE,hint:s.onlineRate+'% 在线率'},{label:'延迟客户端',value:s.states.DELAYED,hint:'需要关注'},{label:'离线客户端',value:s.states.OFFLINE,hint:'连接中断'}]}catch(e){this.warn('心跳数据加载失败',e)}},
    async loadLogs(){try{if(!this.categories.length)this.categories=await this.api('/api/v1/logs/categories');const choices=this.categories.filter(x=>x.platform===this.logPlatform);if(!choices.some(x=>x.key===this.logCategory))this.logCategory=choices[0]?.key||'';const d=await this.api(`/api/v1/logs/categories/${this.logCategory}?platform=${this.logPlatform}&page=1&size=30&keyword=${encodeURIComponent(this.keyword)}`);this.logs=d.items||[];this.logKeys=this.logs.length?Object.keys(this.logs[0]):[]}catch(e){this.warn('日志列表加载失败',e)}},
    async loadEvidence(){try{const [s,d]=await Promise.all([this.api('/api/v1/evidence/summary'),this.api('/api/v1/evidence?page=1&size=100')]);this.evidence=d.items||[];this.evidenceMetrics=[{label:'存证总数',value:s.total,hint:'独立原始记录'},{label:'校验正常',value:s.statuses.VALID,hint:'SHA-256 一致'},{label:'哈希异常',value:s.statuses.HASH_MISMATCH,hint:'需要复核'},{label:'待校验',value:s.statuses.PENDING,hint:'等待校验'}]}catch(e){this.warn('存证数据加载失败',e)}},
    exportLogs(){location.href=`/api/v1/logs/export?platform=${this.logPlatform}&category=${this.logCategory}&size=1000`},
    exportClients(){const text=['clientId,serverName,ipAddress,os,status',...this.clients.map(c=>[c.clientId,c.serverName,c.ipAddress,c.os_type,c.heartbeatStatus].map(x=>'"'+String(x||'').replaceAll('"','""')+'"').join(','))].join('\\n');const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\\ufeff'+text],{type:'text/csv'}));a.download='clients.csv';a.click()},
    connectMonitorWs(){const proto=location.protocol==='https:'?'wss':'ws';this.wsStatus='connecting';let ws;try{ws=new WebSocket(`${proto}://${location.host}/ws/client-monitor`)}catch(e){this.wsStatus='disconnected';this.syncRealtimeMetric();this.scheduleWsReconnect();return}this.ws=ws;this.wsLastMsg=Date.now();ws.onopen=()=>{this.wsStatus='connected';this.wsRetry=0;this.wsLastMsg=Date.now();this.syncRealtimeMetric()};ws.onmessage=(ev)=>{this.wsLastMsg=Date.now();try{this.applySnapshot(JSON.parse(ev.data))}catch(e){}};ws.onclose=()=>{this.wsStatus='disconnected';this.syncRealtimeMetric();this.scheduleWsReconnect()};ws.onerror=()=>{try{ws.close()}catch(e){}}},
    startWsWatchdog(){if(this.wsWatchTimer)return;this.wsWatchTimer=setInterval(()=>{const ws=this.ws;if(ws&&ws.readyState===1&&Date.now()-this.wsLastMsg>20000){try{ws.close()}catch(e){}}},5000)},
    scheduleWsReconnect(){if(this.wsTimer)return;const delay=Math.min(30000,1000*Math.pow(2,this.wsRetry++));this.wsTimer=setTimeout(()=>{this.wsTimer=null;this.connectMonitorWs()},delay)},
    applySnapshot(snap){this.clients=(snap.clients||[]).map(c=>({clientId:c.client_id,serverName:c.hostname,ipAddress:c.ip,os_type:c.system,lastHeartbeatAt:c.last_heartbeat,heartbeatDelaySec:c.heartbeatDelaySec,heartbeatStatus:c.status}));const total=snap.total_clients||0;this.heartbeatMetrics=[{label:'客户端总数',value:total,hint:'纳管客户端'},{label:'在线客户端',value:snap.online_clients||0,hint:(total?Math.round((snap.online_clients||0)/total*100):0)+'% 在线率'},{label:'延迟客户端',value:snap.delayed_clients||0,hint:'需要关注'},{label:'离线客户端',value:snap.offline_clients||0,hint:'连接中断'}]}
  }
}
