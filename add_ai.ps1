$s = Get-Content -Raw -Path "ui/index.html" -Encoding UTF8
if ($s -notmatch "startExplain") {
  $fn = @"

let _aiCtrl=null;
function startExplain(a){
  var body=document.getElementById('aiBody'), who=document.getElementById('aiWho'), caret=document.getElementById('aiCaret'), re=document.getElementById('btnRe'), form=document.getElementById('askForm'), inp=document.getElementById('askInput');
  if(!body) return;
  if(_aiCtrl){ try{_aiCtrl.abort();}catch(e){} }
  _aiCtrl = new AbortController();
  body.textContent=''; if(who) who.textContent='starting\u2026'; if(caret) caret.style.display='inline-block';
  if(re) re.onclick = function(){ startExplain(a); };
  if(form){
    form.onsubmit = async function(ev){
      ev.preventDefault();
      var q = inp.value.trim(); if(!q) return;
      inp.disabled=true;
      try{
        var fd=formData();
        var r = await api('/api/ask',{method:'POST',body:JSON.stringify(Object.assign({},fd,{question:q}))});
        if(body) body.textContent = r.answer;
        if(who) who.textContent = (r.provider === 'vouch-composer') ? 'Vouch composer \u00b7 on-device' : ('AI \u00b7 '+r.provider);
        if(caret) caret.style.display='none';
      }catch(e){ toast(e.message); }
      finally{ inp.disabled=false; inp.value=''; }
    };
  }
  (async function(){
    try {
      var fd2=formData();
      var res = await fetch('/api/explain',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({},fd2,{question:''})), signal:_aiCtrl.signal});
      if(!res.ok) throw new Error('explain failed');
      var rd = res.body.getReader(), dec = new TextDecoder(), buf='';
      while(true){
        var rr = await rd.read(); if(rr.done) break;
        buf += dec.decode(rr.value,{stream:true});
        var idx;
        while((idx=buf.indexOf('\n\n'))>=0){
          var chunk = buf.slice(0,idx); buf = buf.slice(idx+2);
          if(chunk.indexOf('data: ')==0){
            try{
              var ev = JSON.parse(chunk.slice(6));
              if(ev.meta && who){ who.textContent = (ev.meta.provider==='vouch-composer')?'Vouch composer \u00b7 on-device':('AI \u00b7 '+ev.meta.provider); }
              if(ev.delta && body){ body.textContent += ev.delta; }
              if(ev.done && caret){ caret.style.display='none'; }
            }catch(e){}
          }
        }
      }
      if(caret) caret.style.display='none';
    }catch(e){
      if(e.name!=='AbortError'){ if(body) body.textContent='Could not load explanation.'; if(who) who.textContent='error'; if(caret) caret.style.display='none'; }
    }
  })();
}
"@
  $s += $fn
  Set-Content -Path "ui/index.html" -Value $s -Encoding UTF8
  Write-Output "added"
} else {
  Write-Output "exists"
}
