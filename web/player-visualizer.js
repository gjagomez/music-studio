// Analyse the actual player signal; reuse a single media source across song changes.
(()=>{
const audio=document.getElementById('audio'),canvas=document.getElementById('playerWaves'),player=document.getElementById('player');
const paint=canvas.getContext('2d');
if(!paint)return;
let context=null,source=null,analyser=null,bins=null,waveform=null,frame=0,last=0,width=0,height=0,accent='',muted='';
const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
function colors(){const theme=getComputedStyle(document.documentElement);accent=theme.getPropertyValue('--accent').trim()||'#3855ff';muted=theme.getPropertyValue('--muted').trim()||'#96969e';}
function resize(){const rect=canvas.getBoundingClientRect();if(!rect.width||!rect.height)return;const scale=Math.min(window.devicePixelRatio||1,2);width=rect.width;height=rect.height;canvas.width=Math.round(width*scale);canvas.height=Math.round(height*scale);paint.setTransform(scale,0,0,scale,0,0);draw(!audio.paused);}
function draw(active){if(!width)return;paint.clearRect(0,0,width,height);let maximum=0,energy=0,bass=0;
if(active&&analyser){analyser.getByteFrequencyData(bins);analyser.getByteTimeDomainData(waveform);for(let i=0;i<bins.length;i++){maximum=Math.max(maximum,bins[i]/255);if(i<8)bass+=bins[i]/(255*8);}for(const value of waveform)energy+=((value-128)/128)**2;energy=Math.sqrt(energy/waveform.length);}
const light=document.documentElement.dataset.theme==='light',center=height*.74;
if(active&&maximum>.002){const glow=paint.createRadialGradient(width*.5,center,0,width*.5,center,width*.58);glow.addColorStop(0,light?'#db709c':accent);glow.addColorStop(.45,light?'#c084c7':'#7955ff');glow.addColorStop(1,'transparent');paint.globalAlpha=light?Math.min(.19,.03+energy*.35+bass*.06):Math.min(.38,.05+energy*.65+bass*.12);paint.fillStyle=glow;paint.fillRect(0,0,width,height);paint.globalAlpha=1;}
const points=256,values=[];
for(let i=0;i<=points;i++){let value=0;if(active&&waveform){const start=Math.floor(i*(waveform.length-4)/points);for(let j=0;j<4;j++)value+=(waveform[start+j]-128)/(128*4);}const edge=Math.sin(Math.PI*i/points)**.28;values.push([i*width/points,center-Math.tanh(value*2.2)*height*.22*edge]);}
const line=paint.createLinearGradient(0,0,width,0);line.addColorStop(0,light?'#9d174d':'#53bfff');line.addColorStop(.45,light?'#86198f':accent);line.addColorStop(.8,light?'#7e22ce':'#b076ff');line.addColorStop(1,light?'#be123c':'#59ceff');
function stroke(thickness,blur,alpha,color){paint.save();paint.lineJoin='round';paint.lineCap='round';paint.strokeStyle=color;paint.lineWidth=thickness;paint.filter=blur?'blur('+blur+'px)':'none';paint.globalAlpha=alpha;paint.beginPath();for(let i=0;i<values.length;i++)i?paint.lineTo(...values[i]):paint.moveTo(...values[i]);paint.stroke();paint.restore();}
if(active&&maximum>.002){stroke(16+energy*18,16,.35+bass*.3,line);stroke(7,5,.7,line);stroke(light?3:2.1,0,1,line);stroke(.7,0,light?.75:.9,light?'#4a164f':'#eef6ff');}else{stroke(10,12,light?.1:.18,line);stroke(light?2:1.5,0,light?.85:.55,line);}
canvas.dataset.level=maximum.toFixed(3);canvas.dataset.running=String(active&&!!analyser);}
function animate(now){frame=0;if(audio.paused||audio.ended||document.hidden){draw(false);return;}if(now-last>=33){last=now;draw(true);}if(!reduced.matches)frame=requestAnimationFrame(animate);}
function stop(){cancelAnimationFrame(frame);frame=0;draw(false);player.classList.remove('is-playing');}
window.preparePlayerVisualizer=()=>{try{if(!context){const AudioEngine=window.AudioContext||window.webkitAudioContext;if(!AudioEngine)return;context=new AudioEngine();source=context.createMediaElementSource(audio);try{analyser=context.createAnalyser();analyser.fftSize=1024;analyser.smoothingTimeConstant=.78;bins=new Uint8Array(analyser.frequencyBinCount);waveform=new Uint8Array(analyser.fftSize);source.connect(analyser);analyser.connect(context.destination);}catch(error){source.disconnect();source.connect(context.destination);analyser=null;}}if(context.state==='suspended'||context.state==='interrupted')context.resume().catch(()=>{});}catch(error){canvas.hidden=true;}};
function start(){window.preparePlayerVisualizer();colors();resize();player.classList.add('is-playing');if(!frame)frame=requestAnimationFrame(animate);}
audio.addEventListener('play',start);audio.addEventListener('playing',start);
for(const event of ['pause','ended','emptied','error'])audio.addEventListener(event,stop);
audio.addEventListener('waiting',stop);
document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();else if(!audio.paused&&!audio.ended)start();});
reduced.addEventListener('change',()=>{stop();if(!audio.paused)start();});
new ResizeObserver(resize).observe(canvas);
new MutationObserver(()=>{colors();draw(!audio.paused);}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
colors();
})();
