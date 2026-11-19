let imageLibrary=[],selectedImages=[],sceneIndex=0,uploadBusy=false;
function refreshScenePreview(){
  window.updateVideoDesign?.();
  const image=document.getElementById('compositionImage');const view=document.getElementById('compositionPreview');
  const source=imageLibrary.find(i=>i.id===selectedImages[sceneIndex]);
  const clip=sceneClips.find(c=>c.id===document.getElementById('sceneClip').value);const video=document.getElementById('compositionClip');video.hidden=!clip;if(clip){if(video.dataset.clip!==clip.id){video.src=clip.url;video.dataset.clip=clip.id;}if(document.getElementById('videoDialog').open)video.play().catch(()=>{});}else video.pause();
  image.hidden=!source||!!clip;if(source)image.src=source.url;else image.removeAttribute('src');
  view.dataset.layout=document.getElementById('videoLayout').value;view.style.aspectRatio=view.dataset.layout==='vertical'?'9 / 16':'16 / 9';
  view.classList.toggle('moving',!!source&&document.getElementById('imageMotion').checked);
  view.classList.toggle('has-image',!!source||!!clip);view.dataset.palette=document.getElementById('videoPalette').value;
  const text=document.getElementById('compositionText');text.hidden=!document.getElementById('imageText').checked;
  document.getElementById('compositionTitle').textContent=document.getElementById('videoTitle').value;
  document.getElementById('compositionChannel').textContent=document.getElementById('videoChannel').value;
  document.getElementById('sceneLabel').textContent=clip?'Escena IA · se repite durante la canción':selectedImages.length?`Imagen ${sceneIndex+1} de ${selectedImages.length} · vista del diseño`:'Fondo automático · vista del diseño';
}
function drawImageLibrary(){
  const library=document.getElementById('imageLibrary');library.replaceChildren();
  for(const item of imageLibrary){const b=document.createElement('button');b.type='button';b.className='image-thumb';b.classList.toggle('selected',selectedImages.includes(item.id));b.setAttribute('aria-label',selectedImages.includes(item.id)?'Quitar imagen de la secuencia':'Agregar imagen a la secuencia');const img=document.createElement('img');img.src=item.url;img.alt='Imagen subida para el video';b.append(img);b.onclick=()=>{const index=selectedImages.indexOf(item.id);if(index>=0)selectedImages.splice(index,1);else if(selectedImages.length<8)selectedImages.push(item.id);else{document.getElementById('sceneMessage').textContent='Podés usar hasta 8 imágenes por video.';return;}sceneIndex=0;drawImageLibrary();};library.append(b);}
  const sequence=document.getElementById('imageSequence');sequence.replaceChildren();
  selectedImages.forEach((id,index)=>{const item=imageLibrary.find(i=>i.id===id);const row=document.createElement('div');row.className='scene-row';const label=document.createElement('button');label.type='button';label.className='scene-name';label.textContent=`${index+1}. Imagen ${imageLibrary.indexOf(item)+1}`;label.onclick=()=>{sceneIndex=index;refreshScenePreview();};row.append(label);
    for(const [symbol,delta,title] of [['↑',-1,'Mover antes'],['↓',1,'Mover después']]){const b=document.createElement('button');b.type='button';b.textContent=symbol;b.setAttribute('aria-label',title);b.disabled=index+delta<0||index+delta>=selectedImages.length;b.onclick=()=>{[selectedImages[index],selectedImages[index+delta]]=[selectedImages[index+delta],selectedImages[index]];sceneIndex=0;drawImageLibrary();};row.append(b);}
    const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','Quitar de la secuencia');remove.onclick=()=>{selectedImages.splice(index,1);sceneIndex=0;drawImageLibrary();};row.append(remove);sequence.append(row);
  });
  document.getElementById('imageCount').textContent=`${selectedImages.length} / 8 seleccionadas`;refreshScenePreview();
}
async function loadSceneImages(){
  const id=videoSong.id;imageLibrary=[];sceneIndex=0;drawImageLibrary();document.getElementById('sceneMessage').textContent='';
  try{const r=await fetch(`/api/songs/${id}/images`);if(!r.ok)throw Error('No se pudieron cargar las imágenes.');const images=await r.json();if(videoSong?.id!==id)return;imageLibrary=images;selectedImages=selectedImages.filter(id=>images.some(i=>i.id===id));drawImageLibrary();}catch(error){document.getElementById('sceneMessage').textContent=error.message;}
}
async function uploadImages(files){
  if(uploadBusy||!files.length||!videoSong)return;
  const id=videoSong.id;uploadBusy=true;document.getElementById('imageUpload').disabled=true;
  try{for(const file of files){document.getElementById('sceneMessage').textContent=`Subiendo ${file.name}…`;if(file.size>12*1024*1024)throw Error('Cada imagen puede pesar hasta 12 MB.');const form=new FormData();form.append('file',file);const r=await fetch(`/api/songs/${id}/images`,{method:'POST',headers:{'x-studio':'ace-local'},body:form});const item=await r.json();if(!r.ok)throw Error(typeof item.detail==='string'?item.detail:'No se pudo subir la imagen.');if(videoSong?.id!==id)return;imageLibrary.push(item);if(selectedImages.length<8)selectedImages.push(item.id);drawImageLibrary();}document.getElementById('sceneMessage').textContent='Imágenes listas. Revisá el orden y tocá Guardar cambios y exportar.';}
  catch(error){document.getElementById('sceneMessage').textContent=error.message;}
  finally{uploadBusy=false;document.getElementById('imageUpload').disabled=false;document.getElementById('imageUpload').value='';}
}
document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('imageUpload').onchange=e=>uploadImages([...e.target.files]);
  const drop=document.getElementById('imageDrop');drop.ondragover=e=>{e.preventDefault();drop.classList.add('dragging');};drop.ondragleave=()=>drop.classList.remove('dragging');drop.ondrop=e=>{e.preventDefault();drop.classList.remove('dragging');uploadImages([...e.dataTransfer.files]);};
  for(const id of ['videoTitle','videoChannel','videoPalette','imageText','imageMotion'])document.getElementById(id).addEventListener('input',refreshScenePreview);
});
