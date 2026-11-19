let videoState={items:[],busy:false,available:true},videoSong=null,videoSignature='';
function videoEntry(song,layout){return videoState.items.find(v=>v.song_id===song.id&&v.layout===layout);}
function appendVideoActions(song,container){
  if(song.status!=='complete')return;
  const row=document.createElement('div');row.className='video-actions';
  const exists=videoState.items.some(v=>v.song_id===song.id&&v.status==='complete');
  const create=document.createElement('button');create.type='button';create.className='song-action primary-action';create.innerHTML=libraryIcon('video')+'<span>'+(exists?'Editar video':'Crear video')+'</span>';create.onclick=()=>openVideo(song);row.append(create);
  for(const layout of ['horizontal','vertical']){
    const entry=videoEntry(song,layout);if(!entry)continue;
    if(entry.status==='complete'){
      const group=document.createElement('div');group.className='video-export-group';
      const watch=document.createElement('button');watch.type='button';watch.className='song-action';watch.innerHTML=libraryIcon(layout)+'<span>'+(layout==='horizontal'?'YouTube':'Reels / Shorts')+'</span>';watch.setAttribute('aria-label','Ver video '+(layout==='horizontal'?'horizontal':'vertical')+' de '+song.title);watch.onclick=()=>openVideo(song,layout);
      const link=document.createElement('a');link.className='export-download';link.href=`/api/songs/${song.id}/video/${layout}?download=true`;link.download=true;link.innerHTML=libraryIcon('download');link.setAttribute('aria-label','Descargar '+(layout==='horizontal'?'YouTube':'Reels / Shorts')+' de '+song.title);link.title=layout==='horizontal'?'Descargar video para YouTube':'Descargar video para Reels / Shorts';group.append(watch,link);row.append(group);
    }else{const status=document.createElement('span');status.className='video-status '+(entry.status==='rendering'?'rendering':'');status.textContent=entry.status==='rendering'?'Creando video…':'Video: '+entry.message;row.append(status);}
  }
  container.append(row);
}
function updateVideoDialog(){
  if(!videoSong)return;
  const layout=document.getElementById('videoLayout').value;
  const entry=videoEntry(videoSong,layout);
  const preview=document.getElementById('videoPreview');const download=document.getElementById('videoDownload');
  const url=`/api/songs/${videoSong.id}/video/${layout}`;
  if(entry?.status==='complete'){
    if(preview.dataset.source!==url){preview.src=url+'?v='+Date.now();preview.dataset.source=url;}
    preview.hidden=false;download.hidden=false;download.href=url+'?download=true';
  }else{preview.pause();preview.hidden=true;download.hidden=true;preview.removeAttribute('src');preview.dataset.source='';}
  document.getElementById('videoMessage').textContent=!videoState.available?'Falta FFmpeg para exportar videos.':entry?.message||'El fondo y las ondas se crean automáticamente con tu canción.';
  document.getElementById('videoSubmit').disabled=videoState.busy||!videoState.available;
  document.getElementById('videoSubmit').textContent=videoState.busy?'Creando video…':entry?.status==='complete'?'Guardar cambios y exportar':'Crear MP4';
}
function fillVideoEditor(){
  const entry=videoEntry(videoSong,document.getElementById('videoLayout').value);
  document.getElementById('videoTitle').value=entry?.title||videoSong.title;
  document.getElementById('videoLyricsLrc').value=entry?.lyrics_lrc||'';
  document.getElementById('videoLyricsEnabled').checked=!!entry?.lyrics_lrc;
  window.resetLyricTap?.();
  document.getElementById('videoScreenText').value=entry?.screen_text||'';
  document.getElementById('videoTextPosition').value=entry?.text_position||'bottom';
  document.getElementById('videoTextSize').value=entry?.text_size||'medium';
  document.getElementById('videoTextColor').value=entry?.text_color||'#ffffff';
  document.getElementById('videoPalette').value=entry?.palette||'blue';
  document.getElementById('videoVisualizer').value=entry?.visualizer||(entry?'classic':'lightning');
  document.getElementById('videoWaveColor').value=entry?.wave_color||'#705bff';
  document.getElementById('videoStart').value=entry?.start||0;
  document.getElementById('videoEnd').value=entry?.end||videoSong.duration;
  document.getElementById('videoStart').max=videoSong.duration;
  document.getElementById('videoEnd').max=videoSong.duration;
  document.getElementById('videoVolume').value=entry?.volume??1;
  document.getElementById('videoVolumeLabel').textContent=Math.round((entry?.volume??1)*100)+'%';
  document.getElementById('videoWaveform').checked=entry?.waveform??true;
  pendingClip=entry?.clip||'';
  selectedImages=[...(entry?.images||[])];sceneIndex=0;
  document.getElementById('imageMotion').checked=entry?.motion??true;document.getElementById('imageText').checked=entry?.overlay_text??true;
  if(imageLibrary.length)drawImageLibrary();
  let channel='';try{channel=localStorage.getItem('alma-channel')||'';}catch{}
  document.getElementById('videoChannel').value=entry?.channel??channel;
}
function openVideo(song,layout){
  videoSong=song;document.getElementById('videoSongTitle').textContent=song.title;
  document.getElementById('videoLayout').value=layout||videoState.items.find(v=>v.song_id===song.id&&v.status==='complete')?.layout||'horizontal';
  fillVideoEditor();loadSceneImages();loadSceneClips();document.getElementById('videoDialog').showModal();updateVideoDialog();refreshScenePreview();
}
async function refreshVideos(){
  try{const r=await fetch('/api/videos/status');if(!r.ok)return;const next=await r.json();const signature=JSON.stringify(next);videoState=next;if(signature!==videoSignature){videoSignature=signature;if(typeof render==='function')render();}if(document.getElementById('videoDialog').open)updateVideoDialog();}catch{}
}
document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('videoClose').onclick=()=>document.getElementById('videoDialog').close();
  document.getElementById('videoDialog').addEventListener('close',()=>{document.getElementById('videoPreview').pause();document.getElementById('compositionClip').pause();document.getElementById('videoDesignAudio').pause();});
  document.getElementById('videoLayout').onchange=()=>{updateVideoDialog();refreshScenePreview();};
  document.getElementById('videoVolume').oninput=()=>{document.getElementById('videoVolumeLabel').textContent=Math.round(document.getElementById('videoVolume').value*100)+'%';};
  document.getElementById('videoForm').onsubmit=async e=>{
    e.preventDefault();if(!videoSong)return;if(uploadBusy){document.getElementById('videoMessage').textContent='Esperá a que terminen de subir las imágenes.';return;}
    const data={lyrics_lrc:document.getElementById('videoLyricsEnabled').checked?document.getElementById('videoLyricsLrc').value.trim():'',screen_text:document.getElementById('videoScreenText').value.trim(),text_position:document.getElementById('videoTextPosition').value,text_size:document.getElementById('videoTextSize').value,text_color:document.getElementById('videoTextColor').value,visualizer:document.getElementById('videoVisualizer').value,wave_color:document.getElementById('videoWaveColor').value,layout:document.getElementById('videoLayout').value,palette:document.getElementById('videoPalette').value,channel:document.getElementById('videoChannel').value.trim(),title:document.getElementById('videoTitle').value.trim(),start:Number(document.getElementById('videoStart').value),end:Number(document.getElementById('videoEnd').value),volume:Number(document.getElementById('videoVolume').value),waveform:document.getElementById('videoWaveform').checked,images:[...selectedImages],motion:document.getElementById('imageMotion').checked,overlay_text:document.getElementById('imageText').checked,clip:document.getElementById('sceneClip').value};
    if(data.end<=data.start){document.getElementById('videoMessage').textContent='El final debe ser mayor que el inicio.';return;}
    document.getElementById('videoSubmit').disabled=true;
    try{localStorage.setItem('alma-channel',data.channel);}catch{}
    try{const response=await fetch(`/api/songs/${videoSong.id}/video`,{method:'POST',headers:{'Content-Type':'application/json','x-studio':'ace-local'},body:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Revisá los datos del video.');await refreshVideos();}
    catch(error){document.getElementById('videoMessage').textContent=error.message;document.getElementById('videoSubmit').disabled=false;}
  };
  refreshVideos();setInterval(refreshVideos,3000);
});
