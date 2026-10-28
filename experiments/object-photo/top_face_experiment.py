# Experiment: grow the top face (colour tolerance) from the positive point inside the SAM mask; dims after parallax.
# python top_face_experiment.py   (cached SAM only)
import glob, json, os, sys, cv2, numpy as np
sys.path.insert(0,'/home/user/Hephaestus'); sys.path.insert(0,'/home/user/Hephaestus/experiments/object-photo')
os.environ['SAM_DECODE_WORKERS']='0'
from scipy import ndimage
from hephaestus import object_photo as op, sam
import spike
from PIL import Image
def top_face(sheet, region, seed, tol):
    lab = cv2.cvtColor(cv2.GaussianBlur(sheet,(5,5),0), cv2.COLOR_RGB2LAB).astype(np.float32)
    ys,xs = np.nonzero(cv2.circle(np.zeros(region.shape,np.uint8), seed, 12, 1, -1) & region)
    ref = np.median(lab[ys,xs], axis=0)
    close = (np.linalg.norm(lab - ref, axis=2) < tol) & region
    lb,_ = ndimage.label(close)
    k = lb[seed[1], seed[0]]
    if k == 0: return close
    f = lb == k
    f = cv2.morphologyEx(f.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5,5),np.uint8)) > 0
    return f
def oriented(m):
    cnts,_ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    (cx,cy),(w,h),a = cv2.minAreaRect(max(cnts, key=cv2.contourArea))
    return sorted([w/op.PX_PER_MM, h/op.PX_PER_MM], reverse=True)
seg = sam.Segmenter(offline=True); tiles=[]
for jpg in sorted(glob.glob('/home/user/Hephaestus/experiments/object-photo/photos/*.jpg')):
    t=json.load(open(jpg[:-4]+'.json')); data,img=op.load_rgb(jpg)
    quad,_=op.find_sheet(img,data,seg); size,Hs=op.sheet_frame(quad); cam=op.camera_from_sheet(Hs,(img.shape[1],img.shape[0]))
    sheet=op.rectify(img,Hs,size); pr=op.object_prompt(sheet)
    raw = op.sam_object(sheet, seg, pr, "box_point")
    region = cv2.dilate(ndimage.binary_fill_holes(op.clean_mask(raw, sheet)).astype(np.uint8), np.ones((9,9),np.uint8)).astype(bool)
    true_dims = sorted(t['bbox_mm'][:2], reverse=True)
    out=[]
    for tol in (6, 10, 14):
        f = top_face(sheet, region, pr['point'], tol)
        c,s,_,_ = op.parallax_correct(f, cam, t['thickness_mm'])
        out.append((tol, [round(d,1) for d in oriented(ndimage.binary_fill_holes(c))]))
        if tol == 10:
            im = (sheet*0.7+76).astype(np.uint8); cnts,_=cv2.findContours(c.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE); cv2.drawContours(im,cnts,-1,(212,68,28),1)
            x1,y1,x2,y2=pr['box']; tl=Image.fromarray(im[y1:y2,x1:x2]); tl.thumbnail((300,300)); tiles.append(tl)
    print(os.path.basename(jpg)[:-4], 'true', true_dims, 'top face (tol, dims after parallax):', out)
W=Image.new('RGB',(300*4,300*2),'white')
for k,c in enumerate(tiles): W.paste(c,((k%4)*300,(k//4)*300))
W.save('results/top_face_growing.png')
