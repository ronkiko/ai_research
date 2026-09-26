let socket=null;
let inputSequence=0;

function emitAck(name,payload){
  if(!socket?.connected)return Promise.reject(Error('Player Gateway не подключён'));
  return new Promise((resolve,reject)=>{
    socket.timeout(1500).emit(name,payload,(error,value)=>{
      if(error)return reject(Error('Player Gateway timeout'));
      if(!value?.ok)return reject(Error(value?.error||'Player Gateway отклонил запрос'));
      resolve(value);
    });
  });
}

export function connectFrames({onFrame,onOpen,onError,onStatus,onInput}){
  if(typeof window.io!=='function')throw Error('Socket.IO transport unavailable');
  socket=window.io({transports:['websocket','polling']});
  socket.on('connect',()=>onOpen?.());
  socket.on('connect_error',(error)=>onError?.(error));
  socket.on('disconnect',()=>onError?.(Error('Player Gateway disconnected')));
  socket.on('frame.latest',(data)=>{
    if(data?.version!==1||!data.frame)return;
    onFrame(data.frame,Boolean(data.reset));
  });
  socket.on('gateway.status',(data)=>onStatus?.(data));
  socket.on('input.applied',(data)=>onInput?.(data));
  return socket;
}

export function gatewayAcquire(){
  return emitAck('control.acquire',{version:1});
}

export function gatewayInput(axis_x){
  inputSequence+=1;
  return emitAck('input_state',{version:1,sequence:inputSequence,axis_x});
}

export function gatewayRelease(){
  return emitAck('control.release',{version:1});
}
