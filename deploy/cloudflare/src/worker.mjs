import {DurableObject} from 'cloudflare:workers';
import {containerClass, handlers} from './platform.mjs';

export class FarCareApi extends containerClass(DurableObject, 'api') {}
export class FarCareWeb extends containerClass(DurableObject, 'web') {}
export default handlers;
