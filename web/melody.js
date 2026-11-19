/* Local piano and microphone capture. Only an explicitly selected recording is uploaded. */
let selectedMelody=null;
(()=>{
const el=id=>document.getElementById(id);
let ctx,bus,recordKind=null,previewURL=null,uploading=false;
const voices=new Map(),keys=['a','w','s','e','d','f','t','g','y','h','u','j','k'];
function message(text){el('melodyMessage').textContent=text;const chip=el('selectedGuide');if(chip){chip.hidden=!selectedMelody;chip.textContent=selectedMelody?'♫ Melodía guía lista · se usará para crear la canción':'';}}
function audio(){if(!ctx){ctx=new AudioContext();bus=ctx.createGain();bus.gain.value=.18;bus.connect(ctx.destination);}return ctx.resume();}
function noteOn(n){if(voices.has(n))return;audio();const osc=ctx.createOscillator(),gain=ctx.createGain();osc.type='triangle';osc.frequency.value=440*2**((60+n-69)/12);gain.gain.setValueAtTime(0,ctx.currentTime);gain.gain.linearRampToValueAtTime(1,ctx.currentTime+.015);osc.connect(gain);gain.connect(bus);osc.start();voices.set(n,{osc,gain});el('pianoKeys').children[n].classList.add('pressed');}
function noteOff(n){const v=voices.get(n);if(!v)return;v.gain.gain.setTargetAtTime(0,ctx.currentTime,.03);v.osc.stop(ctx.currentTime+.2);voices.delete(n);el('pianoKeys').children[n].classList.remove('pressed');}
function release(){for(const n of [...voices.keys()])noteOff(n);}
const names=['Do','Do♯','Re','Re♯','Mi','Fa','Fa♯','Sol','Sol♯','La','La♯','Si','Do'];
names.forEach((name,n)=>{const key=document.createElement('button');key.type='button';key.className='piano-key'+(name.includes('♯')?' sharp':'');key.textContent=name;key.setAttribute('aria-label',name+' · tecla '+keys[n].toUpperCase());key.onpointerdown=e=>{e.preventDefault();key.setPointerCapture(e.pointerId);noteOn(n);};key.onpointerup=()=>noteOff(n);key.onpointercancel=()=>noteOff(n);el('pianoKeys').append(key);});
window.addEventListener('keydown',e=>{if(el('humDialog')?.open&&!window.pianoPopupOpen?.())return;if(e.repeat||e.ctrlKey||e.metaKey||e.altKey||/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)||(!window.pianoPopupOpen?.()&&(/BUTTON/.test(e.target.tagName)||!el('melodyPanel').open)))return;const n=keys.indexOf(e.key.toLowerCase());if(n>=0){e.preventDefault();noteOn(n);}});
window.addEventListener('keyup',e=>{const n=keys.indexOf(e.key.toLowerCase());if(n>=0)noteOff(n);});window.addEventListener('blur',release);
function controls(){const active=!!recordKind||uploading;el('recordPiano').disabled=active;el('recordHum').disabled=active;el('melodyFile').disabled=active;el('removeMelody').disabled=active;}
async function upload(blob,name){uploading=true;controls();message('Preparando tu melodía…');try{const body=new FormData();body.append('file',blob,name);const r=await fetch('/api/melodies',{method:'POST',headers:{'x-studio':'ace-local'},body});const result=await r.json();if(!r.ok)throw Error(typeof result.detail==='string'?result.detail:'No se pudo guardar el audio.');selectedMelody=result.id;el('duration').disabled=true;if(previewURL)URL.revokeObjectURL(previewURL);previewURL=null;el('melodyPreview').src=result.url;el('melodyPreview').hidden=false;el('melodyDuration').textContent=result.duration+' s';message('Melodía lista. Elegí el estilo y tocá Crear canción.');return true;}catch(e){message(e.message);}finally{uploading=false;el('melodyFile').value='';controls();}}
el('recordPiano').onclick=()=>window.openPianoRecorder();el('recordHum').onclick=()=>window.openHumRecorder();
el('melodyFile').onchange=()=>{const file=el('melodyFile').files[0];if(file)upload(file,file.name);};
el('removeMelody').onclick=()=>{delete el('songForm').dataset.requiredGuide;selectedMelody=null;el('duration').disabled=false;el('melodyPreview').pause();el('melodyPreview').removeAttribute('src');el('melodyPreview').hidden=true;el('melodyDuration').textContent='';message('Crearás música sin melodía guía.');};
window.createPianoCapture=async()=>{await audio();release();const destination=ctx.createMediaStreamDestination();bus.connect(destination);let disconnected=false;return {stream:destination.stream,release:()=>{if(disconnected)return;disconnected=true;release();bus.disconnect(destination);destination.stream.getTracks().forEach(track=>track.stop());}};};
window.releasePianoNotes=release;
window.useMelodyAudio=upload;
window.melodyBusy=()=>!!recordKind||uploading||!!window.humBusy?.();
window.restoreMelody=song=>{if(recordKind||uploading)return;selectedMelody=song.melody_id||null;el('duration').disabled=!!selectedMelody;el('melodyStrength').value=song.melody_strength??.6;el('melodyPreview').hidden=!selectedMelody;if(selectedMelody)el('melodyPreview').src='/api/melodies/'+selectedMelody;else el('melodyPreview').removeAttribute('src');el('melodyDuration').textContent='';message(selectedMelody?'Melodía recuperada para esta versión.':'Podés agregar una melodía o crear solo con texto.');};
})();
