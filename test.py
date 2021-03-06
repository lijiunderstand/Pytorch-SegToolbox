import torch
import torch.nn.functional as F
import numpy as np
import matplotlib.pyplot as plt

from tqdm import tqdm
from PIL import Image
from train import get_model
from datasets.laneseg import LaneSegDataset
from utils.tools import now_str, get_metrics, get_confusion_matrix, get_logger
from utils.augment import PairCrop, PairNormalizeToTensor, PairResize


def test(net, data, device, resize_to=256, n_class=8, compare=False):
    """
    测试
    :param net: AI网络
    :param data: test dataset
    :param device: torch.device GPU or CPU
    :param n_class: n种分类
    :param compare: 是否生成对比图片
    :return:
    """
    net.to(device)
    net.eval()  # 测试
    total_cm = np.zeros((n_class, n_class))  # 记录整个测试的混淆矩阵
    total_batch_miou = 0.  # 累加每张图像的mIoU

    offset = 690  # 剪裁690x3384
    pair_crop = PairCrop(offsets=(offset, None))  # 剪裁690x3384
    pair_resize = PairResize(size=resize_to)
    pair_norm_to_tensor = PairNormalizeToTensor(norm=True)  # 归一化并正则化

    with torch.no_grad():  # 测试阶段，不需要计算梯度，节省内存
        bar_format = '{desc}{postfix}|{n_fmt}/{total_fmt}|{percentage:3.0f}%|{bar}|{elapsed}<{remaining}'
        # {desc}{进度条百分比}[{当前/总数}{用时<剩余时间}{自己指定的后面显示的}]
        tqdm_data = tqdm(data, ncols=120, bar_format=bar_format, desc='Test')
        for i_batch, (im, lb) in enumerate(tqdm_data, start=1):
            # if i_batch > 1:
            #     break
            im_t, lb_t = pair_crop(im, lb)  # PIL Image,PIL Image
            im_t, lb_t = pair_resize(im_t, lb_t)  # PIL Image,PIL Image
            im_t, lb_t = pair_norm_to_tensor(im_t, lb_t)  # [C,H,W]tensor,[H,W]tensor

            im_t = im_t.to(device)  # [C,H,W]tensor装入GPU
            im_t = im_t.unsqueeze(0)  # 转换为[N,C,H,W] tensor
            output = net(im_t)  # 经过模型输出[N,C,H,W] tensor
            pred = torch.argmax(F.softmax(output, dim=1), dim=1)  # [N,H,W] tensor

            pred = pred.unsqueeze(1)  # [N,C,H,W] tensor, F.interpolate操作图像需要[N,C,H,W] tensor
            pred = pred.type(torch.float)  # 转为float数，F.interpolate只对float类型操作，int，long等都没有实现
            pred = F.interpolate(pred, size=(lb.size[1] - offset, lb.size[0]),
                                 mode='nearest')  # pred用nearest差值
            pred = pred.type(torch.uint8)  # 再转回int类型
            pred = pred.squeeze(0).squeeze(0)  # [H,W]tensor
            pred = pred.cpu().numpy()  # [H,W]ndarray

            supplement = np.zeros((offset, lb.size[0]), dtype=np.uint8)  # [H,W]ndarray,补充成背景
            pred = np.append(supplement, pred, axis=0)  # 最终的估值，[H,W]ndarray,在H方向cat，给pred补充被剪裁的690x3384
            batch_cm = get_confusion_matrix(pred, lb, n_class)  # 本张图像的混淆矩阵
            total_cm += batch_cm  # 累加

            if compare:  # 生成对比图
                fontsize = 16  # 图像文字字体大小
                fig, ax = plt.subplots(2, 2, figsize=(20, 15))  # 画布
                ax = ax.flatten()

                ax[0].imshow(im)  # 左上角显示原图
                ax[0].set_title('Input Image', fontsize=fontsize)  # 标题

                ax[1].imshow(LaneSegDataset.decode_rgb(np.asarray(lb)))  # 右上角显示 Grand Truth
                ax[1].set_title('Grand Truth', fontsize=fontsize)  # 标题

                batch_miou = get_metrics(batch_cm, metrics='mean_iou')  # 计算本张图像的mIoU
                fig.suptitle('mIoU:{:.4f}'.format(batch_miou), fontsize=fontsize)  # 用mIoU作为大标题
                total_batch_miou += batch_miou

                mask = (pred != 0).astype(np.uint8) * 255  # [H,W]ndarray,alpha融合的mask

                pred = LaneSegDataset.decode_rgb(pred)  # [H,W,C=3]ndarray RGB
                ax[3].imshow(pred)  # 右下角显示Pred
                ax[3].set_title('Pred', fontsize=fontsize)  # 标题

                mask = mask[..., np.newaxis]  # [H,W,C=1]ndarray
                pred = np.append(pred, mask, axis=2)  # [H,W,C=4]ndarray，RGB+alpha变为RGBA

                im = im.convert('RGBA')
                pred = Image.fromarray(pred).convert('RGBA')
                im_comp = Image.alpha_composite(im, pred)  # alpha融合
                ax[2].imshow(im_comp)  # 左下角显示融合图像
                ax[2].set_title('Pred over Input', fontsize=fontsize)  # 标题

                plt.subplots_adjust(left=0.01, bottom=0.01, right=0.99, top=0.99,
                                    wspace=0.01, hspace=0.01)  # 调整子图边距间距
                plt.savefig('/home/mist/imfolder/pred-{:s}.jpg'.format(now_str()))  # 保存图像
                plt.close(fig)
                pass
            tqdm_str = 'mIoU={:.4f}|bat_mIoU={:.4f}'  # 进度条
            tqdm_data.set_postfix_str(
                tqdm_str.format(get_metrics(total_cm),
                                total_batch_miou / i_batch))
            pass
        mean_iou = get_metrics(total_cm)  # 整个测试的mIoU
        total_batch_miou /= len(data)

        logger = get_logger()
        msg = ('Test mIoU : {:.4f}|'
               'Test bat_mIoU : {:.4f}').format(mean_iou, total_batch_miou)
        logger.info(msg)
        return mean_iou


if __name__ == '__main__':
    dev = torch.device('cuda:0')  # 选择一个可用的GPU
    load_file = ('/home/mist/Pytorch-SegToolbox/res/preds'
                 'deeplabv3p_xception-2020-03-27-10-44-08-epoch-10.pth')  # 读取训练好的参数
    # load_file = None
    mod = get_model('deeplabv3p_xception',
                    in_channels=3, n_class=8, device=dev, load_weight=load_file)
    # model = DeepLabV3P('xception', 3, 8)
    # wt = torch.load(load_file, map_location=dev)
    # model.load_state_dict(wt)
    s = input('->')
    test(net=mod,
         data=LaneSegDataset('test'),  # 不剪裁，不缩放的测试集，读取PIL Image
         resize_to=578,  # 这里指定缩放大小
         n_class=8,
         device=dev,
         compare=True)
    pass
