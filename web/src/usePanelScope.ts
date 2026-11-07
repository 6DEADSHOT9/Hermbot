import {useRef} from 'react';

// Closing or retargeting a panel invalidates callbacks from its prior requests.
export function usePanelScope(key:string) {
  const scope=useRef({key,generation:0});
  if(scope.current.key!==key)scope.current={key,generation:scope.current.generation+1};
  const generation=scope.current.generation;
  return ()=>scope.current.generation===generation;
}
