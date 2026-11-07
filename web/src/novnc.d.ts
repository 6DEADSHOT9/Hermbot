declare module '@novnc/novnc' {
  export default class RFB {
    constructor(target:HTMLElement,url:string,options?:Record<string,unknown>);
    viewOnly:boolean;
    scaleViewport:boolean;
    resizeSession:boolean;
    background:string;
    disconnect():void;
    focus():void;
    sendKey(keysym:number,code?:string,down?:boolean):void;
    addEventListener(name:string,listener:(event:CustomEvent)=>void):void;
  }
}
