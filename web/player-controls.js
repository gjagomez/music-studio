(()=>{
const audio=document.getElementById('audio'),controls=document.getElementById('playbackControls'),toggle=document.getElementById('playerToggle'),seek=document.getElementById('playerSeek'),time=document.getElementById('playerTime'),mute=document.getElementById('playerMute'),volume=document.getElementById('playerVolume');
let dragging=false;
const clock=value=>{const seconds=Math.max(0,Math.floor(Number.isFinite(value)?value:0));return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');};
function update(){const duration=Number.isFinite(audio.duration)?audio.duration:0,position=audio.currentTime||0,available=!!(audio.currentSrc||audio.getAttribute('src'));toggle.disabled=!available;seek.disabled=!duration;if(!dragging){seek.value=duration?position/duration*100:0;seek.style.setProperty('--seek-progress',seek.value+'%');}time.textContent=clock(position)+' / '+clock(duration);seek.setAttribute('aria-valuetext',clock(position)+' de '+clock(duration));const playing=!audio.paused&&!audio.ended;toggle.setAttribute('aria-label',playing?'Pausar':'Reproducir');toggle.innerHTML=playing?'<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>':'<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';}
toggle.onclick=()=>{if(audio.paused){window.preparePlayerVisualizer?.();audio.play().catch(()=>update());}else audio.pause();};
seek.addEventListener('pointerdown',()=>{dragging=true;});
seek.addEventListener('input',()=>{if(Number.isFinite(audio.duration)&&audio.duration>0){audio.currentTime=Number(seek.value)/100*audio.duration;seek.style.setProperty('--seek-progress',seek.value+'%');time.textContent=clock(audio.currentTime)+' / '+clock(audio.duration);seek.setAttribute('aria-valuetext',clock(audio.currentTime)+' de '+clock(audio.duration));}});
function release(){if(dragging){dragging=false;update();}}
window.addEventListener('pointerup',release);seek.addEventListener('pointercancel',release);seek.addEventListener('change',release);
function sound(){volume.value=audio.muted?0:audio.volume;mute.setAttribute('aria-label',audio.muted||audio.volume===0?'Activar sonido':'Silenciar');mute.classList.toggle('is-muted',audio.muted||audio.volume===0);mute.setAttribute('aria-pressed',String(audio.muted));}
mute.onclick=()=>{audio.muted=!audio.muted;};volume.oninput=()=>{audio.volume=Number(volume.value);audio.muted=audio.volume===0;};
for(const event of ['loadedmetadata','durationchange','timeupdate','play','pause','ended','emptied','loadstart','error'])audio.addEventListener(event,update);
audio.addEventListener('volumechange',sound);
audio.controls=false;audio.hidden=true;controls.hidden=false;update();sound();
})();
