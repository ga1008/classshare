import {describe,it,expect,vi,afterEach} from 'vitest';
import {normalizeColor,hexToHsv,hsvToHex,commonColors,recordColor,rankedColors} from '../ui_color_model.js';
import {createPopoverSystem} from '../ui_popover.js';
import {gradientCss,DEFAULT_GRADIENT} from './appearance_controls.js';
import {configureColorPicker} from '../ui_color_picker.js';
import {assignStageLesson} from './home_controls.js';

describe('shared color contract',()=>{
    it('moves a lesson between visual groups without duplicate membership or lost lessons',()=>{
        const stages=[{lessons:[1]},{lessons:[2,3]},{lessons:[]}];assignStageLesson(stages,2,3,true);expect(stages.map(s=>s.lessons)).toEqual([[1],[2],[3]]);assignStageLesson(stages,2,3,false);expect(stages.map(s=>s.lessons)).toEqual([[1],[2],[]]);
    });
    it('normalizes supported hex and tokens without allowing arbitrary CSS',()=>{
        expect(normalizeColor(' #aBc ')).toBe('#aabbcc');expect(normalizeColor('#abcd')).toBe('#aabbccdd');expect(normalizeColor('#123456ff')).toBe('#123456');
        expect(normalizeColor('PRIMARY')).toBe('primary');
        for(const invalid of ['#12345','#1234567','rgb(1,2,3)','url(x)','__proto__','red'])expect(normalizeColor(invalid)).toBe('');
    });
    it('preserves RGB and alpha through the rectangular HSV plane',()=>{
        for(const color of ['#000000','#ffffff','#808080','#ff0000','#00ff00','#0000ff','#123456','#12345680','#abcdef00','#10203f01'])expect(hsvToHex(hexToHsv(color))).toBe(color);
        for(let n=0;n<256;n+=7){const hex='#'+[n,255-n,Math.floor(n/2)].map(v=>v.toString(16).padStart(2,'0')).join('');expect(hsvToHex(hexToHsv(hex))).toBe(hex);}
    });
    it('sorts common colors by frequency, uses recency for ties and shares normalized entries',()=>{
        let data=recordColor([], '#abc',1);data=recordColor(data,'#000000',2);expect(commonColors(data)[0]).toBe('#000000');
        data=recordColor(data,'#aabbcc',3);expect(data[0]).toEqual({color:'#aabbcc',count:2,last:3});expect(commonColors(data)).toHaveLength(10);
        expect(commonColors(data).filter(c=>c==='#aabbcc')).toHaveLength(1);
    });
    it('tolerates corrupt storage and bounds growth and counters',()=>{
        expect(commonColors({bad:true})).toHaveLength(10);
        expect(rankedColors([null,{color:'#000',count:NaN},{color:'url(x)',count:5}])).toEqual([]);
        const many=Array.from({length:100},(_,i)=>({color:'#'+i.toString(16).padStart(6,'0'),count:1,last:i}));expect(rankedColors(many)).toHaveLength(48);
        expect(recordColor([{color:'#000000',count:1e6,last:0}],'#000000')[0].count).toBe(1e6);
    });
    it('previews complete gradients using current document theme colors',()=>{
        configureColorPicker({resolveColor:c=>({'primary':'#0284c7','primary-dark':'#075985'}[c]||c)});
        expect(gradientCss(DEFAULT_GRADIENT)).toBe('linear-gradient(135deg,#0284c7,#075985)');expect(gradientCss({from:'#fff'})).toBe('');
    });
});

describe('nested shared popover lifecycle',()=>{
    afterEach(()=>vi.unstubAllGlobals());
    function setup(){
        vi.stubGlobal('document',{addEventListener:vi.fn(),removeEventListener:vi.fn()});vi.stubGlobal('window',{addEventListener:vi.fn(),removeEventListener:vi.fn()});
        const manager=createPopoverSystem().popoverManager;
        const item=(name,parent)=>({name,options:{parent:parent?'anchor':undefined},anchor:parent?.panel,panel:{style:{},contains(node){return node===this;}},close:vi.fn(function(reason){this.reason=reason;manager.released(this);})});
        return {manager,item};
    }
    it('keeps the modal alive and closes only its previous child when another color opens',()=>{
        const {manager,item}=setup(),modal=item('background');manager.open(modal);const first=item('from',modal);manager.open(first);
        expect(modal.close).not.toHaveBeenCalled();const second=item('to',modal);manager.open(second);
        expect(first.close).toHaveBeenCalledWith('replaced');expect(manager.current).toBe(second);second.close();expect(manager.current).toBe(modal);
    });
    it('closing or replacing a parent releases children and all event subscriptions',()=>{
        const {manager,item}=setup(),modal=item('background');manager.open(modal);const child=item('color',modal);manager.open(child);modal.close();
        expect(child.close).toHaveBeenCalledWith('parent-closed');expect(manager.isOpen()).toBe(false);expect(manager.listening).toBe(false);
        manager.open(modal);manager.open(child);const other=item('media');manager.open(other);expect(manager.stack).toEqual([other]);manager.closeAll();expect(manager.stack).toEqual([]);
    });
});
