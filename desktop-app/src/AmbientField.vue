<script setup lang="ts">
import {onMounted,onUnmounted,ref,watch} from 'vue'
const props=defineProps<{reduced:boolean;dark:boolean;palette:string}>()
const canvas=ref<HTMLCanvasElement>()
let observer:ResizeObserver|undefined,frame=0,last=0,phase=0,width=0,height=0,context:CanvasRenderingContext2D|null=null
const query=window.matchMedia('(prefers-reduced-motion: reduce)')
let pointer={x:.72,y:.3},target={...pointer}
const tones:Record<string,number[]>={home:[39,146,148],network:[35,157,133],quota:[152,119,218],team:[74,158,201],storage:[192,139,72]}
function draw(){
 const element=canvas.value;if(!element||!context||!width||!height)return
 const c=context,[r,g,b]=tones[props.palette]||tones.network,color=(a:number)=>`rgba(${r},${g},${b},${a})`
 c.clearRect(0,0,width,height)
 const glow=c.createRadialGradient(width*pointer.x,height*pointer.y,0,width*pointer.x,height*pointer.y,width*.65)
 glow.addColorStop(0,color(props.dark?.09:.065));glow.addColorStop(.65,color(.018));glow.addColorStop(1,color(0));c.fillStyle=glow;c.fillRect(0,0,width,height)
 const horizon=height*.62,vanish=width*.68
 c.lineWidth=.7;c.strokeStyle=color(props.dark?.13:.075)
 for(let i=-7;i<=9;i++){c.beginPath();c.moveTo(vanish+i*15,horizon);c.lineTo(vanish+i*width*.13,height);c.stroke()}
 for(let i=0;i<8;i++){const t=((i/8+phase*.012)%1),y=horizon+(height-horizon)*t*t;c.beginPath();c.moveTo(0,y);c.lineTo(width,y);c.stroke()}
 for(let i=0;i<22;i++){
  const x=((i*173.7+Math.sin(phase*.08+i)*22)%Math.max(width,1)+width)%width,y=((i*83.3-phase*(3+i%4))%Math.max(height,1)+height)%height
  c.fillStyle=color((props.dark?.28:.2)*(.55+.45*Math.sin(phase*.5+i)));c.beginPath();c.arc(x,y,i%6===0?1.5:.8,0,Math.PI*2);c.fill()
 }
 // Three short signal paths; text surfaces remain opaque above this field.
 for(let i=0;i<3;i++){const y=height*(.22+i*.21),x=(phase*(14+i*5)+i*width/3)%(width+160)-160
  const beam=c.createLinearGradient(x,y,x+90,y);beam.addColorStop(0,color(0));beam.addColorStop(1,color(props.dark?.24:.16));c.strokeStyle=beam;c.beginPath();c.moveTo(x,y);c.lineTo(x+72,y);c.lineTo(x+89,y-17);c.stroke()
 }
}
function tick(time:number){
 if(time-last>=40){phase+=Math.min((time-last)/1000,.08);last=time;pointer.x+=(target.x-pointer.x)*.055;pointer.y+=(target.y-pointer.y)*.055;draw()}
 frame=requestAnimationFrame(tick)
}
function sync(){
 cancelAnimationFrame(frame);frame=0
 const moving=!props.reduced&&!query.matches&&!document.hidden
 if(canvas.value)canvas.value.dataset.motion=moving?'running':'static'
 draw();if(moving){last=performance.now();frame=requestAnimationFrame(tick)}
}
function resize(){
 const element=canvas.value;if(!element)return
 const rect=element.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,1.75)
 width=rect.width;height=rect.height;element.width=Math.round(width*dpr);element.height=Math.round(height*dpr)
 context=element.getContext('2d');context?.setTransform(dpr,0,0,dpr,0,0);draw()
}
function move(event:PointerEvent){const rect=canvas.value?.getBoundingClientRect();if(rect&&rect.width&&event.pointerType!=='touch')target={x:(event.clientX-rect.left)/rect.width,y:(event.clientY-rect.top)/rect.height}}
watch(()=>[props.reduced,props.dark,props.palette],sync)
onMounted(()=>{observer=new ResizeObserver(resize);observer.observe(canvas.value!);window.addEventListener('pointermove',move,{passive:true});document.addEventListener('visibilitychange',sync);query.addEventListener('change',sync);resize();sync()})
onUnmounted(()=>{cancelAnimationFrame(frame);observer?.disconnect();window.removeEventListener('pointermove',move);document.removeEventListener('visibilitychange',sync);query.removeEventListener('change',sync)})
</script>
<template><canvas ref="canvas" class="ambient-field" aria-hidden="true"></canvas></template>
<style scoped>.ambient-field{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:0;contain:strict}</style>
