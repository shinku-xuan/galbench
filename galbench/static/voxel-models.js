import {voxelCastModel} from './voxel-cast.js';

// Packed standing characters; reference design and source-model rights stay separate.
const cache=new Map();
const limit=(v,a,b)=>Math.max(a,Math.min(b,v));
export function voxelModel(kind='robot'){
  if(!cache.has(kind)){
    const model=voxelCastModel(kind);if(!model)return null;
    cache.set(kind,model);
  }
  return cache.get(kind);
}
export function voxelPreview(kind,yaw=-.35,pitch=.14){
  const m=voxelModel(kind);if(!m)return null;
  const cy=Math.cos(yaw),sy=Math.sin(yaw),cx=Math.cos(pitch),sx=Math.sin(pitch);
  const rotate=([x,y,z])=>{const px=x*cy+z*sy,pz=z*cy-x*sy;return [px,y*cx-pz*sx,y*sx+pz*cx];};
  const visible=m.previewFaces.map(f=>({normal:rotate(f.normal),color:f.color,ao:f.ao,vertices:f.vertices.map(rotate)})).filter(f=>f.normal[2]>.00001);
  visible.sort((a,b)=>a.vertices.reduce((n,v)=>n+v[2],0)-b.vertices.reduce((n,v)=>n+v[2],0));
  let left=Infinity,right=-Infinity,top=Infinity,bottom=-Infinity;
  const runs=[];
  for(const f of visible){
    const intensity=limit(.85+f.normal[1]*.14-f.normal[0]*.12+f.normal[2]*.1,.65,1.06)*(f.ao?1-(1-f.ao.reduce((s,v)=>s+v,0)/4)*(m.aoStrength??1):1);
    const rgb=[1,3,5].map(i=>Math.min(255,Math.round(parseInt(f.color.slice(i,i+2),16)*intensity))),color=`rgb(${rgb.join(',')})`;
    const points=f.vertices.map(v=>{left=Math.min(left,v[0]);right=Math.max(right,v[0]);top=Math.min(top,-v[1]);bottom=Math.max(bottom,-v[1]);return v[0].toFixed(2)+','+(-v[1]).toFixed(2);});
    const path=`M${points.join('L')}Z`,last=runs[runs.length-1];
    if(last?.color===color)last.paths.push(path);else runs.push({color,paths:[path]});
  }
  const paths=runs.map(r=>`<path d="${r.paths.join('')}" fill="${r.color}" stroke="${r.color}" stroke-width=".12"/>`);
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${left-1} ${top-1} ${right-left+2} ${bottom-top+2}">${paths.join('')}</svg>`;
}
