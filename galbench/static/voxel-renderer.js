import {ACESFilmicToneMapping,BufferGeometry,Color,DirectionalLight,Float32BufferAttribute,Group,HemisphereLight,Mesh,MeshLambertMaterial,OrthographicCamera,Scene,SRGBColorSpace,WebGLRenderer} from "./three.vendor.js";
import {voxelModel} from "./voxel-models.js";

// One context and one merged surface mesh per figure; hidden voxels have no faces.
export function createVoxelRenderer(onContextChange){
  const canvas=document.createElement("canvas");canvas.className="voxel-canvas";canvas.setAttribute("aria-hidden","true");
  const context=canvas.getContext("webgl2",{alpha:true,antialias:true,powerPreference:"low-power",premultipliedAlpha:true});
  if(!context)return null;
  const renderer=new WebGLRenderer({canvas,context,alpha:true,antialias:true});
  renderer.setClearColor(0,0);renderer.outputColorSpace=SRGBColorSpace;renderer.toneMapping=ACESFilmicToneMapping;renderer.toneMappingExposure=.92;
  const scene=new Scene(),camera=new OrthographicCamera(0,1,1,0,-2000,2000);camera.position.z=800;
  scene.add(new HemisphereLight(0xffffff,0xc4bace,1.65));
  const light=new DirectionalLight(0xfff8ed,1.05);light.position.set(-100,250,400);scene.add(light);
  const material=new MeshLambertMaterial({vertexColors:true});
  const pixelMaterial=new MeshLambertMaterial({vertexColors:true});
  pixelMaterial.onBeforeCompile=shader=>{
    shader.vertexShader='attribute vec3 voxelUV; varying vec3 vVoxelUV;\n'+shader.vertexShader;
    shader.vertexShader=shader.vertexShader.replace('#include <begin_vertex>','#include <begin_vertex>\nvVoxelUV=voxelUV;');
    shader.fragmentShader='varying vec3 vVoxelUV;\n'+shader.fragmentShader;
    shader.fragmentShader=shader.fragmentShader.replace('#include <color_fragment>','#include <color_fragment>\nvec2 edge=min(vVoxelUV.xy,1.0-vVoxelUV.xy);\ndiffuseColor.rgb*=mix(1.0,mix(.84,1.0,smoothstep(.015,.07,min(edge.x,edge.y))),vVoxelUV.z);');
  };
  pixelMaterial.customProgramCacheKey=()=> 'galbench-pixel-voxel-v1';
  const emptyMaterial=new MeshLambertMaterial({vertexColors:true,transparent:true,opacity:.22,depthWrite:false});
  const geometries=new Map();let actors=[],stage=null,frame=0,active=false,auto=false,lost=false,last=0,lastPaint=0,layoutDirty=true,frames=0,width=0,height=0;

  function geometry(kind){
    if(geometries.has(kind))return geometries.get(kind);
    const model=voxelModel(kind);if(!model||!model.faces?.length)return null;
    const positions=[],normals=[],colors=[],uvs=[],palette=new Map();
    for(const face of model.faces){
      let color=palette.get(face.color);if(!color){color=new Color(face.color);palette.set(face.color,color);}
      for(const index of face.diagonal?[1,2,3,1,3,0]:[0,1,2,0,2,3]){
        positions.push(...face.vertices[index]);normals.push(...face.normal);
        const shade=face.ao?Math.pow(1-(1-face.ao[index])*(model.aoStrength??1),2.2):1;colors.push(color.r*shade,color.g*shade,color.b*shade);
        uvs.push(...[[0,0],[1,0],[1,1],[0,1]][index],model.gridStrength??1);
      }
    }
    const g=new BufferGeometry();g.setAttribute("position",new Float32BufferAttribute(positions,3));g.setAttribute("normal",new Float32BufferAttribute(normals,3));g.setAttribute("color",new Float32BufferAttribute(colors,3));
    if(model.renderStyle==='pixel')g.setAttribute('voxelUV',new Float32BufferAttribute(uvs,3));
    g.computeBoundingSphere();geometries.set(kind,g);return g;
  }

  function attach(nextStage,entries){
    for(const actor of actors)if(actor.group)scene.remove(actor.group);
    actors=[];stage=nextStage;stage.prepend(canvas);
    entries.forEach((entry,index)=>{
      const g=geometry(entry.kind);
      if(!g){actors.push({...entry,index,group:null});return;}
      const group=new Group(),mesh=new Mesh(g,entry.empty?emptyMaterial:voxelModel(entry.kind).renderStyle==='pixel'?pixelMaterial:material);group.add(mesh);scene.add(group);
      actors.push({...entry,index,group,yaw:-.32+index*.07,pitch:.14,velocity:0,resumeAt:0,dragging:false});
    });
    layoutDirty=true;last=0;lastPaint=0;
  }
  function layout(){
    if(!stage?.isConnected)return;
    const r=canvas.getBoundingClientRect();if(r.width<1||r.height<1)return;
    if(width!==r.width||height!==r.height){
      width=r.width;height=r.height;renderer.setPixelRatio(Math.min(devicePixelRatio||1,innerWidth<=700?1.25:1.5));renderer.setSize(width,height,false);
      camera.left=0;camera.right=width;camera.top=height;camera.bottom=0;camera.updateProjectionMatrix();
    }
    for(const actor of actors){
      if(!actor.group)continue;
      const box=actor.anchor.getBoundingClientRect(),b=voxelModel(actor.kind).bounds;
      const scale=Math.min(box.height/(b.max[1]-b.min[1])*.92,box.width/Math.max(b.max[0]-b.min[0],14)*.95);
      actor.group.scale.setScalar(scale);actor.group.position.set(box.left-r.left+box.width/2,r.bottom-box.bottom+3,0);
    }
    layoutDirty=false;
  }
  function request(){if(active&&!lost&&!frame)frame=requestAnimationFrame(paint);}
  function paint(time){
    frame=0;if(!active||lost||!stage?.isConnected)return;
    const interval=innerWidth<=700?1000/30:1000/60;
    if(auto&&time-lastPaint<interval-1){request();return;}
    const dt=last?Math.min((time-last)/1000,.06):0;last=time;lastPaint=time;
    if(layoutDirty)layout();
    for(const a of actors){
      if(!a.group)continue;
      if(auto&&!a.empty&&!a.dragging){
        if(time>a.resumeAt)a.yaw+=dt*Math.PI*2/55;
        else if(Math.abs(a.velocity)>.003){a.yaw+=a.velocity*dt;a.velocity*=Math.exp(-9*dt);}
      }
      a.group.rotation.set(a.pitch,a.yaw,0);
    }
    renderer.render(scene,camera);frames++;
    if(auto)request();
  }
  function setActive(value,spin){active=value;auto=!!spin;if(!active){cancelAnimationFrame(frame);frame=0;last=0;}else{last=0;request();}}
  function dirty(){layoutDirty=true;request();}
  function drag(index,dx,dy,velocity=0){const a=actors[index];if(!a?.group)return;a.yaw+=dx*.014;a.pitch=Math.max(-.08,Math.min(.36,a.pitch+dy*.007));a.velocity=velocity;a.resumeAt=performance.now()+2500;request();}
  function dragging(index,value){const a=actors[index];if(a?.group){a.dragging=value;if(!value)a.resumeAt=performance.now()+2500;}}
  function reset(index){for(const a of actors)if(a.group&&(index==null||index===a.index)){a.yaw=-.32+a.index*.07;a.pitch=.14;a.velocity=0;a.resumeAt=performance.now()+2500;}request();}
  canvas.addEventListener("webglcontextlost",event=>{event.preventDefault();lost=true;cancelAnimationFrame(frame);frame=0;onContextChange(false);});
  canvas.addEventListener("webglcontextrestored",()=>{lost=false;layoutDirty=true;onContextChange(true);request();});
  function stats(){return {frames,active,auto,lost,figures:actors.filter(a=>a.group).length,drawCalls:renderer.info.render.calls,triangles:renderer.info.render.triangles,pixelRatio:renderer.getPixelRatio(),geometries:renderer.info.memory.geometries,kinds:actors.map(a=>a.group?a.kind:null),poses:actors.map(a=>a.group?{yaw:a.yaw,pitch:a.pitch}:null)};}
  function dispose(){setActive(false,false);for(const g of geometries.values())g.dispose();geometries.clear();material.dispose();pixelMaterial.dispose();emptyMaterial.dispose();renderer.dispose();renderer.forceContextLoss();canvas.remove();actors=[];stage=null;}
  return {canvas,attach,setActive,dirty,drag,dragging,reset,stats,dispose};
}
