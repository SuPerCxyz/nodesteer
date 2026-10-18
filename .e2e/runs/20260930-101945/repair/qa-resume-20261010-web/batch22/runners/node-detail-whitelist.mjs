import {execFileSync} from 'node:child_process';
const S='qawebsecure20261010-b18-clean38181';
const expression=`(async()=>{
 const route=location.pathname; const id=route.split('/').pop();
 if(!route.startsWith('/agents/')||!id) return JSON.stringify({guard:false});
 const token=localStorage.getItem('nodesteer_token')||''; const response=await fetch('/api/nodes/'+encodeURIComponent(id),{headers:token?{Authorization:'Bearer '+token}:{}}); const node=await response.json();
 const card=[...document.querySelectorAll('main [data-slot="card"]')].find(c=>(c.querySelector('[data-slot="card-title"]')?.innerText||'').trim()==='清单');
 const fields={}; if(card) for(const d of card.querySelectorAll('dt')) fields[(d.innerText||'').trim()]=(d.parentElement?.querySelector('dd')?.innerText||'').trim();
 const inv=node.inventory||{}; const norm=x=>String(x??'').replace(/\\s+/g,' ').trim();
 const expected={
  os:[inv.os,inv.os_version].filter(Boolean).join(' ')||'-',
  kernel:inv.kernel||'-',
  cpu:inv.cpu?.map(x=>x.model+' ('+x.cores+')').join(', ')||'-',
  memory:inv.memory?Math.round(inv.memory.total_kb/1024)+' MB':'-',
  filesystem:inv.filesystem?.length?inv.filesystem.map(x=>x.mount+' ('+x.fs_type+', '+Math.round(x.free_kb/1024)+'/'+Math.round(x.total_kb/1024)+' MB)').join(' '):'-',
  network:inv.network?.map(x=>x.interface+': '+x.addresses.join(', ')).join('; ')||'-'
 };
 const labelMap={os:'系统',kernel:'内核',cpu:'CPU',memory:'内存',filesystem:'文件系统',network:'网络'};
 const encoder=new TextEncoder(); const hash=async x=>{const z=await crypto.subtle.digest('SHA-256',encoder.encode(norm(x)));return [...new Uint8Array(z)].map(v=>v.toString(16).padStart(2,'0')).join('')};
 const inventory={apiStatus:response.status,idMatches:node.id===id,cardPresent:!!card,apiInventoryPresent:!!node.inventory,apiKeys:Object.keys(inv).sort(),fields:{}};
 for(const [k,label] of Object.entries(labelMap)){const ui=fields[label];inventory.fields[k]={apiPresent:Object.prototype.hasOwnProperty.call(inv,k==='filesystem'?'filesystem':k),apiType:Array.isArray(inv[k])?'array':typeof inv[k],uiFieldPresent:ui!==undefined,apiHash:await hash(expected[k]),uiHash:await hash(ui===undefined?'':ui),matches:ui!==undefined&&norm(ui)===norm(expected[k]),apiArrayLength:Array.isArray(inv[k])?inv[k].length:null}}
 const caps=node.capabilities||{}; const capNodes=[...(card?.querySelectorAll('[title]')||[])].filter(e=>e.classList.contains('font-mono'));
 let enabled=0,disabled=0,matched=0,enabledMatched=0,disabledMatched=0;for(const [key,value] of Object.entries(caps)){const el=capNodes.find(e=>e.getAttribute('title')===key);if(value===true)enabled++;if(value===false)disabled++;if(el){matched++;const text=(el.parentElement?.innerText||'').toLowerCase();if(value===true&&(text.includes('已启用')||text.includes('enabled')))enabledMatched++;if(value===false&&(text.includes('已禁用')||text.includes('disabled')))disabledMatched++}}
 return JSON.stringify({guard:true,routeClass:'/agents/<id>',nodeIdSha256:await hash(id),apiStatus:response.status,nodeResponseIdMatchesRoute:node.id===id,inventory,capabilities:{apiCount:Object.keys(caps).length,enabled,disabled,uiKeyMatches:matched,enabledStateMatches:enabledMatched,disabledStateMatches:disabledMatched,apiKeysetSha256:await hash(Object.keys(caps).sort().join('|'))}})
})()`;
const out=execFileSync('agent-browser',['--session',S,'eval',expression],{encoding:'utf8'}).trim();let v;try{v=JSON.parse(out)}catch{v=JSON.parse(JSON.parse(out))}if(typeof v==='string')v=JSON.parse(v);console.log(JSON.stringify(v));
