import {voxelCastData} from './voxel-cast-data.js';

// Decode only the character data, never the scripts or UI from a supplied viewer.
// Local coordinates are x-right, y-up, z-front; the origin follows the feet.
export function voxelCastModel(kind){
  if(!Object.hasOwn(voxelCastData,kind))return null;
  const record=voxelCastData[kind];
  const [width,height,depth]=record.size,raw=atob(record.data),grid=new Uint8Array(width*height*depth);
  const index=(x,y,z)=>x+width*(z+depth*y);
  const at=(x,y,z)=>x<0||y<0||z<0||x>=width||y>=height||z>=depth?0:grid[index(x,y,z)];
  for(let i=0;i<raw.length;i+=4)grid[index(raw.charCodeAt(i),raw.charCodeAt(i+1),raw.charCodeAt(i+2))]=raw.charCodeAt(i+3);
  const faces=[],min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity],levels=[.55,.72,.86,1];
  for(let i=0;i<raw.length;i+=4){
    const xyz=[raw.charCodeAt(i),raw.charCodeAt(i+1),raw.charCodeAt(i+2)],color=record.palette[raw.charCodeAt(i+3)];
    xyz.forEach((v,j)=>{min[j]=Math.min(min[j],v-record.origin[j]);max[j]=Math.max(max[j],v+1-record.origin[j]);});
    for(let axis=0;axis<3;axis++)for(const sign of [-1,1]){
      const outside=xyz.slice();outside[axis]+=sign;if(at(...outside))continue;
      const u=(axis+1)%3,v=(axis+2)%3,vertices=[],ao=[],order=[[-1,-1],[1,-1],[1,1],[-1,1]];
      if(sign<0)order.reverse();
      for(const [du,dv] of order){
        const a=outside.slice(),b=outside.slice(),c=outside.slice();a[u]+=du;b[v]+=dv;c[u]+=du;c[v]+=dv;
        const sideA=!!at(...a),sideB=!!at(...b),corner=!!at(...c);
        ao.push(levels[sideA&&sideB?0:3-Number(sideA)-Number(sideB)-Number(corner)]);
        const point=xyz.map((n,j)=>n+.5-record.origin[j]);point[axis]+=sign*.5;point[u]+=du*.5;point[v]+=dv*.5;vertices.push(point);
      }
      const normal=[0,0,0];normal[axis]=sign;
      faces.push({normal,color,vertices,ao,diagonal:ao[0]+ao[2]<ao[1]+ao[3]});
    }
  }
  return {kind,voxels:raw.length/4,voxelSize:1,faces,previewFaces:faces,bounds:{min,max},renderStyle:'pixel',aoStrength:record.aoStrength??1,gridStrength:record.gridStrength??1};
}
