function appendLibraryActions(song,container){
  if(['generating','queued'].includes(song.status))return;
  const button=document.createElement('button');button.type='button';button.className='song-action danger-action';button.innerHTML=libraryIcon('trash')+'<span>Eliminar</span>';button.setAttribute('aria-label','Eliminar '+song.title);
  button.onclick=async()=>{
    if(!confirm(`¿Mover “${song.title}” y sus videos a la papelera? Podés restaurarlos después.`))return;
    try{
      const r=await fetch(`/api/songs/${song.id}`,{method:'DELETE',headers:{'x-studio':'ace-local'}});const result=await r.json();if(!r.ok)throw Error(result.detail);
      if(selectedId===song.id){document.getElementById('audio').pause();document.getElementById('audio').removeAttribute('src');document.getElementById('playingTitle').textContent='Elegí una canción';document.getElementById('playingStyle').textContent='Tu música empieza acá';document.getElementById('download').hidden=true;selectedId=null;}
      if(videoSong?.id===song.id)document.getElementById('videoDialog').close();
      await refresh();await refreshVideos();
    }catch(error){alert(error.message||'No se pudo eliminar la canción.');}
  };container.append(button);
}
async function showTrash(){
  const dialog=document.getElementById('trashDialog');const list=document.getElementById('trashList');list.replaceChildren();if(!dialog.open)dialog.showModal();
  try{const r=await fetch('/api/trash');if(!r.ok)throw Error('No se pudo abrir la papelera.');const items=await r.json();
    if(!items.length){list.textContent='La papelera está vacía.';return;}
    for(const song of items){const row=document.createElement('div');row.className='trash-row';const title=document.createElement('span');title.textContent=song.title;const restore=document.createElement('button');restore.className='prepare';restore.type='button';restore.textContent='Restaurar';restore.onclick=async()=>{restore.disabled=true;try{const r=await fetch(`/api/trash/${song.id}/restore`,{method:'POST',headers:{'x-studio':'ace-local'}});const result=await r.json();if(!r.ok)throw Error(result.detail);await showTrash();await refresh();await refreshVideos();}catch(error){alert(error.message);restore.disabled=false;}};row.append(title,restore);list.append(row);}
  }catch(error){list.textContent=error.message;}
}
document.addEventListener('DOMContentLoaded',()=>{document.getElementById('trashTab').onclick=showTrash;document.getElementById('trashClose').onclick=()=>document.getElementById('trashDialog').close();});
