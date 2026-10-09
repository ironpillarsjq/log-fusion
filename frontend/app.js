function dashboard() {
  return {
    page:'overview',clock:'',summary:{total:0,by_platform:{Linux:0,Windows:0},categories:[]},categories:[],clients:[],logs:[],evidence:[],logKeys:[],logPlatform:'Linux',logCategory:'authentication_session',keyword:'',heartbeatMetrics:[],evidenceMetrics:[],overviewMetrics:[],
    fmt(v){return Number(v||0).toLocaleString('zh-CN')},
    async api(path){const r=await fetch(path);if(!r.ok)throw Error('请求失败');const j=await r.json();return j.data??j},
    async init(){this.tick();setInterval(()=>this.tick(),1000);await this.refreshSummary();this.categories=await this.api('/api/v1/logs/categories');this.connectSse()},
    tick(){this.clock=new Date().toLocaleString('zh-CN',{hour12:false})},
    async refreshSummary(){try{this.summary=await this.api('/api/v1/logs/summary');this.overviewMetrics=[{label:'日志总量',value:this.fmt(this.summary.total),hint:'实时统计'},{label:'Linux 日志',value:this.fmt(this.summary.by_platform.Linux),hint:'审计日志'},{label:'Windows 日志',value:this.fmt(this.summary.by_platform.Windows),hint:'事件日志'},{label:'实时通道',value:'正常',hint:'SSE 已连接'}];this.drawChart()}catch(e){}},
    drawChart(){const el=document.getElementById('category-chart');if(!el||!window.echarts)return;const chart=echarts.init(el);chart.setOption({tooltip:{trigger:'item'},legend:{bottom:0},series:[{type:'pie',radius:['42%','72%'],data:(this.summary.categories||[]).map(x=>({name:x.platform+' · '+x.label,value:x.count}))}]})},
    async loadHeartbeats(){const [s,d]=await Promise.all([this.api('/api/v1/monitor/summary'),this.api('/api/v1/monitor/clients')]);this.clients=d.items||[];this.heartbeatMetrics=[{label:'客户端总数',value:s.total,hint:'纳管客户端'},{label:'在线客户端',value:s.states.ONLINE,hint:s.onlineRate+'% 在线率'},{label:'延迟客户端',value:s.states.DELAYED,hint:'需要关注'},{label:'离线客户端',value:s.states.OFFLINE,hint:'连接中断'}]},
    async loadLogs(){if(!this.categories.length)this.categories=await this.api('/api/v1/logs/categories');const choices=this.categories.filter(x=>x.platform===this.logPlatform);if(!choices.some(x=>x.key===this.logCategory))this.logCategory=choices[0]?.key||'';const d=await this.api(`/api/v1/logs/categories/${this.logCategory}?platform=${this.logPlatform}&page=1&size=30&keyword=${encodeURIComponent(this.keyword)}`);this.logs=d.items||[];this.logKeys=this.logs.length?Object.keys(this.logs[0]):[]},
    async loadEvidence(){const [s,d]=await Promise.all([this.api('/api/v1/evidence/summary'),this.api('/api/v1/evidence?page=1&size=100')]);this.evidence=d.items||[];this.evidenceMetrics=[{label:'存证总数',value:s.total,hint:'独立原始记录'},{label:'校验正常',value:s.statuses.VALID,hint:'SHA-256 一致'},{label:'哈希异常',value:s.statuses.HASH_MISMATCH,hint:'需要复核'},{label:'待校验',value:s.statuses.PENDING,hint:'等待校验'}]},
    exportLogs(){location.href=`/api/v1/logs/export?platform=${this.logPlatform}&category=${this.logCategory}&size=1000`},
    exportClients(){const text=['clientId,serverName,ipAddress,os,status',...this.clients.map(c=>[c.clientId,c.serverName,c.ipAddress,c.os_type,c.heartbeatStatus].map(x=>'"'+String(x||'').replaceAll('"','""')+'"').join(','))].join('\\n');const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\\ufeff'+text],{type:'text/csv'}));a.download='clients.csv';a.click()},
    connectSse(){const s=new EventSource('/api/v1/logs/stream');s.addEventListener('log',()=>this.refreshSummary())}
  }
}
