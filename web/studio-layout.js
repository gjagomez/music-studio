// Keep the full editor available while making the four main entry points easy to find.
document.querySelectorAll('[data-open-tool]').forEach(button=>button.addEventListener('click',()=>{
  const tool=button.dataset.openTool;
  if(tool==='hum'){openHumRecorder();return;}
  if(tool==='piano'){openPianoRecorder();return;}
  if(tool==='lyrics'){
    setMode(false);$('lyricsPanel').open=true;$('lyrics').focus();$('lyricsPanel').scrollIntoView({behavior:'smooth',block:'nearest'});
  }else{
    $('melodyPanel').open=true;
    $('melodyPanel').scrollIntoView({behavior:'smooth',block:'nearest'});
    if(tool==='upload')$('melodyFile').click();
    else if(tool==='hum')$('recordHum').focus();
    else $('pianoKeys').querySelector('button').focus();
  }
}));
$('songSearch').addEventListener('input',render);
$('songSort').addEventListener('change',render);
$('customMode').addEventListener('click',()=>{$('lyricsPanel').open=true;});

for(const [label, entries] of catalog){const group=document.createElement('optgroup');group.label=label;for(const [value,name] of entries)group.append(new Option(name,value));$('recordingStyle').append(group);}
$('recordingStyle').value=$('styleSelect').value;
$('recordingStyle').addEventListener('change',()=>{setStyle($('recordingStyle').value);saveDraft();});
