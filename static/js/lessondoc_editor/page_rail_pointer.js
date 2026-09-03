// Geometry is measured in track coordinates. A card always owns its full face,
// even where the floating insertion button's larger hit area overlaps an edge.
export function pageRailTarget(x,y,cards) {
    if(!cards.length||y<0||y>cards[0].bottom+10)return null;
    const card=cards.find(c=>x>=c.left&&x<=c.right&&y>=c.top&&y<=c.bottom);
    if(card)return {kind:'card',id:card.id,index:card.index};
    const index=cards.findIndex(c=>x<c.left),gap=index<0?cards.length:index;
    const left=gap?cards[gap-1].right:0,right=cards[gap]?.left??cards.at(-1).right+26;
    return x>=left&&x<=right?{kind:'gap',index:gap}:null;
}

export class PageRailPress {
    constructor(target,x,y,scrollLeft,maxScroll) {
        this.target=target;this.x=x;this.y=y;this.lastX=x;
        this.scrollLeft=scrollLeft;this.maxScroll=maxScroll;this.moved=false;
    }
    move(x,y) {
        if(!this.moved&&Math.hypot(x-this.x,y-this.y)<=5)return;
        this.moved=true;
        // Incremental clamping keeps a reversal responsive after reaching an end.
        if(this.target.kind==='card')this.scrollLeft=Math.max(0,Math.min(this.maxScroll,this.scrollLeft-(x-this.lastX)));
        this.lastX=x;
    }
}
