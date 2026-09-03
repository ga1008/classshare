import { clone, freshInstance } from './model.js';

export const LAYOUTS = {title:'封面',section:'章节',content:'正文','two-col':'双栏',center:'居中',grid:'网格',end:'结尾',canvas:'自由画布'};
export const THEMES = {sky:'天蓝',teal:'青绿',violet:'紫罗兰',amber:'琥珀',rose:'玫瑰',slate:'石板'};
const text = {type:'text',md:'在这里输入内容'};
const frame = {x:80,y:100,w:480,h:220,r:0,z:0};
const entries = [
    ['text','文字','内容',{md:'在这里输入内容'}],
    ['cards','卡片','内容',{cols:2,items:[{title:'关键概念',text:'概念说明'},{title:'应用场景',text:'场景说明'}]}],
    ['bignum','醒目数字','内容',{items:[{value:'01',label:'核心目标'},{value:'02',label:'实践任务'}]}],
    ['bigmark','醒目标记','内容',{mark:'?',line:'提出一个值得思考的问题'}],
    ['timeline','时间线','内容',{items:[{title:'开始',text:'了解问题'},{title:'探索',text:'提出方法'},{title:'总结',text:'验证结论'}]}],
    ['table','表格','内容',{head:['项目','说明'],rows:[['概念','定义'],['应用','示例']]}],
    ['callout','提示框','内容',{tone:'think',md:'想一想：你会怎样解决这个问题？'}],
    ['tabs','标签页','交互',{tabs:[{label:'概念',blocks:[text]},{label:'示例',blocks:[{...text,md:'补充一个示例'}]}]}],
    ['details','折叠详情','交互',{summary:'展开查看解释',blocks:[text]}],
    ['code','代码','代码',{code:'print("Hello")',output:'Hello'}],
    ['media','图片 / 音视频','素材',{kind:'image',src:''}],
    ['svg','SVG 图形','素材',{viewBox:'0 0 640 300',body:'<rect x="40" y="60" width="560" height="180" rx="24" fill="var(--dg-primary-soft)" stroke="var(--dg-primary)"/><text x="320" y="160" text-anchor="middle" font-size="28" fill="var(--dg-text)">示意图</text>'}],
    ['diagram','结构图','素材',{kind:'flow',nodes:[{id:'start',label:'开始'},{id:'finish',label:'完成'}],edges:[{from:'start',to:'finish',label:'行动'}]}],
    ['quiz','小测验','交互',{q:'下列哪一项是正确的？',options:[{k:'A',text:'选项一'},{k:'B',text:'选项二'}],answer:'A',explain:'在这里填写解析'}],
    ['tasklist','任务清单','交互',{items:[{text:'阅读关键概念'},{text:'完成实践任务'}]}],
    ['reveal','点击揭晓','交互',{items:[{label:'查看答案',md:'在这里填写答案'}]}],
    ['stepper','分步演示','交互',{stage:{type:'svg',viewBox:'0 0 640 200',body:'<circle id="point" cx="160" cy="100" r="40" fill="var(--dg-primary)"/>'},steps:[{text:'观察初始位置'},{text:'移动到新位置',set:[{target:'#point',attr:'cx',value:'480'}]}]}],
    ['button','动作按钮','交互',{label:'点击开始',variant:'primary',size:'md',actions:[]}],
    ['codewalk','代码演示','代码',{title:'执行过程',lang:'Python',speedMs:900,lines:[{code:'value = 1',note:'赋初值'},{code:'print(value)',out:'1',note:'输出结果'}]}],
    ['group','组合','布局',{frame:{...frame,w:560,h:260},natural:{w:560,h:260},children:[{...text,frame:{x:20,y:20,w:520,h:80}},{type:'callout',tone:'info',md:'组合中的提示',frame:{x:20,y:120,w:520,h:110}}]}],
    ['html','HTML 片段','代码',{body:'<div class="note"><h3>自定义内容</h3><p>在此编写结构与样式。</p></div>',css:'.note { padding: 24px; border: 2px solid var(--primary); border-radius: 16px; }'}],
];
export const REGISTRY = Object.fromEntries(entries.map(([type,label,category,defaults])=>[type,{type,label,category,defaults}]));
export function makeBlock(type, { positioned = false, resource = null } = {}) {
    const entry=REGISTRY[type]; if(!entry)throw new Error('未知元素类型');
    if(type==='media'&&!resource?.src)throw new Error('请先上传或选择包内素材');
    const block={type,...clone(entry.defaults)};
    if(resource)Object.assign(block,{src:resource.src,kind:resource.kind||'image',caption:''});
    if(positioned&&!block.frame)block.frame={...frame,h:['button','text','bigmark'].includes(type)?100:320};
    return freshInstance(block);
}
export const FIELD_LABELS = {
    md:'内容',text:'文字',title:'标题',name:'元素名称',label:'标签',code:'源码',output:'输出',lang:'语言',
    q:'题干',question:'题干',answer:'正确答案',explain:'解析',summary:'摘要',caption:'说明',src:'包内素材路径',poster:'视频封面',
    mark:'大标记',line:'说明文字',cols:'列数',value:'数值',note:'解释',tone:'语义色',icon:'图标文字',
    items:'条目',head:'表头',rows:'数据行',options:'选项',k:'选项标记',tabs:'标签页',blocks:'内容块',
    lines:'执行轨迹',ref:'引用源码行（从 0 起）',out:'本步输出',speedMs:'每步毫秒',loop:'循环播放',autoStart:'自动开始',
    arrow:'显示箭头',showOutput:'显示输出',showNotes:'显示解释',runLabel:'运行按钮文字',
    body:'结构代码',css:'局部 CSS',viewBox:'SVG 视域',maxWidth:'最大宽度',kind:'类型',variant:'按钮样式',size:'按钮尺寸',
    nodes:'节点',edges:'连线',actors:'参与者',messages:'消息',layers:'层级',links:'连线',children:'子项',
    root:'根节点',from:'起点',to:'终点',id:'标识',steps:'步骤',stage:'舞台',set:'属性变化',show:'显示目标',hide:'隐藏目标',
    target:'目标',attr:'属性',href:'包内链接',collapsed:'初始折叠',step:'出现次序',rowStep:'逐行出现',
};
export const FIELD_OPTIONS = {tone:{primary:'主题',info:'信息',think:'思考',ok:'成功',warn:'提醒',err:'错误'},variant:{primary:'实心',outline:'描边',ghost:'轻量',link:'链接'},size:{sm:'小',md:'中',lg:'大'}};
export function contentFields(block) {
    return Object.keys({...REGISTRY[block.type]?.defaults,...block}).filter((key)=>!['type','id','name','frame','flowFrame','natural','style','hidden','actions','once','skipCovers','excludeSlides'].includes(key));
}
