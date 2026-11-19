let sceneClips=[],pendingClip='';
async function loadSceneClips(){
  const id=videoSong.id;const select=document.getElementById('sceneClip');select.replaceChildren(new Option('Usar mis imágenes / fondo automático',''));
  try{const r=await fetch(`/api/songs/${id}/clips`);if(!r.ok)throw Error('No se pudieron cargar las escenas.');const items=await r.json();if(videoSong?.id!==id)return;sceneClips=items;for(const clip of items)select.append(new Option(`${clip.name} · ${clip.duration.toFixed(1)} s`,clip.id));select.value=pendingClip;refreshScenePreview();}catch(error){document.getElementById('clipMessage').textContent=error.message;}
}
document.addEventListener('DOMContentLoaded',()=>{
  fetch('/api/wan/status').then(r=>r.json()).then(s=>{document.getElementById('openWan').hidden=!s.installed;}).catch(()=>{});
  document.getElementById('openWan').onclick=async()=>{try{const r=await fetch('/api/wan/open',{method:'POST',headers:{'x-studio':'ace-local'}});const result=await r.json();if(!r.ok)throw Error(result.detail);document.getElementById('clipMessage').textContent='Wan Video se abre en Terminal: elegí una escena. Al terminar, subí aquí el MP4 guardado en Películas → Wan-Video.';}catch(error){document.getElementById('clipMessage').textContent=error.message;}};
  document.getElementById('sceneClip').onchange=()=>{pendingClip=document.getElementById('sceneClip').value;refreshScenePreview();};
  document.getElementById('clipUpload').onchange=async e=>{
    const file=e.target.files[0];if(!file||!videoSong)return;const id=videoSong.id;const input=e.target;
    if(file.size>100*1024*1024){document.getElementById('clipMessage').textContent='El video puede pesar hasta 100 MB.';return;}
    uploadBusy=true;input.disabled=true;document.getElementById('clipMessage').textContent='Importando la escena…';
    try{const body=new FormData();body.append('file',file);const r=await fetch(`/api/songs/${id}/clips`,{method:'POST',headers:{'x-studio':'ace-local'},body});const item=await r.json();if(!r.ok)throw Error(item.detail);if(videoSong?.id!==id)return;pendingClip=item.id;await loadSceneClips();document.getElementById('clipMessage').textContent='Escena lista. Se repetirá para cubrir la duración de tu canción; el audio del clip no se utiliza.';}
    catch(error){document.getElementById('clipMessage').textContent=error.message;}
    finally{uploadBusy=false;input.disabled=false;input.value='';}
  };
});
