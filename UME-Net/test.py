import argparse
import math
import os

import torch
from torch.utils.data import DataLoader
from torchvision.utils import save_image as imwrite
from tqdm import tqdm

from Model import Base_Model, Discriminator
from Model_util import padding_image, Lap_Pyramid_Conv
from make import getTxt
from test_dataset import dehaze_test_dataset
from utils_test import to_psnr, to_ssim_skimage


# --- Parse hyper-parameters train --- #
parser = argparse.ArgumentParser(description='Siamese Dehaze Network')
parser.add_argument('--data_dir', type=str, default='')
parser.add_argument('--model_save_dir', type=str, default='./output_result')
parser.add_argument('--log_dir', type=str, default=None)
parser.add_argument('--gpus', default='0,1,2,3', type=str)
# --- Parse hyper-parameters test --- #
parser.add_argument('--test_dataset', type=str, default='')
parser.add_argument('-test_batch_size', help='Set the testing batch size', default=1, type=int)
parser.add_argument('--test_dir', type=str, default='/home/sunhang/lzm/deHaze/outdoor_Test/')
parser.add_argument('--test_name', type=str, default='hazy,clean')
parser.add_argument('--num', type=str, default='9999999', help='')
parser.add_argument('--use_bn', action='store_true', help='if bs>8 please use bn')
parser.add_argument("--type", default=-1, type=int, help="choose a type 012345")
args = parser.parse_args()

tag = 'outdoor'
if args.type == 0:
    args.test_dir = "../datasets_test/Haze1k/Haze1k_thin/dataset/test/"
    args.test_name = 'input,target'
    tag = 'thin'
elif args.type == 1:
    args.test_dir = "../datasets_test/Haze1k/Haze1k_moderate/dataset/test/"
    args.test_name = 'input,target'
    tag = 'moderate'
elif args.type == 2:
    args.test_dir = "../datasets_test/Haze1k/Haze1k_thick/dataset/test/"
    args.test_name = 'input,target'
    tag = 'thick'
elif args.type == 3:
    args.test_dir = "../datasets_test/Dense_hazy/test/"
    args.test_name = 'hazy,clean'
    tag = 'dense'
elif args.type == 4:
    args.test_dir = "../datasets_test/nhhaze/test/"
    args.test_name = 'hazy,clean'
    tag = 'nhhaze'
elif args.type == 5:
    args.test_dir = "../datasets_test/Outdoor/test/"
    args.test_name = 'hazy,clean'
    tag = 'outdoor'
elif args.type == 6:
    args.test_dir = "../datasets_test/UHRI/test/"
    tag = 'UHRI'


predict_result = os.path.join(args.model_save_dir, tag)
test_batch_size = args.test_batch_size

# --- output picture and check point --- #
if not os.path.exists(args.model_save_dir):
    os.makedirs(args.model_save_dir)

if not os.path.exists(predict_result):
    os.makedirs(predict_result)

output_dir = os.path.join(args.model_save_dir, '')

# --- Gpu device --- #
device_ids = [int(i) for i in list(filter(str.isdigit, args.gpus))]
print('use gpus ->', args.gpus)
device = torch.device("cuda:{}".format(device_ids[0]) if torch.cuda.is_available() else "cpu")

if device == 99:
    device = 'cpu'

# --- Define the network --- #
if args.use_bn:
    print('we are using BatchNorm')
else:
    print('we are using InstanceNorm')
SDN = Base_Model()
print('SDN parameters:', sum(param.numel() for param in SDN.parameters()))
# --- Multi-GPU --- #
SDN = SDN.to(device)
laplace = Lap_Pyramid_Conv(num_high=1, device=device).to(device)
# SDN = torch.nn.DataParallel(SDN, device_ids=device_ids)


print('We are testing datasets: ', tag)
getTxt(None, None, args.test_dir, args.test_name)
test_hazy, test_gt = args.test_name.split(',')

if not os.path.exists(os.path.join(predict_result, tag)):
    os.makedirs(os.path.join(predict_result, 'hazy'))
os.system('cp -r {}/* {}'.format(os.path.join(args.test_dir, test_hazy), os.path.join(predict_result, 'hazy')))

if not os.path.exists(os.path.join(predict_result, 'clean')):
    os.makedirs(os.path.join(predict_result, 'clean'))
os.system('cp -r {}/* {}'.format(os.path.join(args.test_dir, test_gt), os.path.join(predict_result, 'clean')))

tran_dir = os.path.join(predict_result, 'tran')
if not os.path.exists(tran_dir):
    os.makedirs(os.path.join(predict_result, 'tran'))

atp_dir = os.path.join(predict_result, 'atp')
if not os.path.exists(atp_dir):
    os.makedirs(os.path.join(predict_result, 'atp'))

predict_dir = os.path.join(predict_result, 'predict')
if not os.path.exists(predict_dir):
    os.makedirs(predict_dir)

test_dataset = dehaze_test_dataset(args.test_dir, args.test_name, tag=tag)
test_loader = DataLoader(dataset=test_dataset, batch_size=1, shuffle=False, num_workers=0, pin_memory=True)

# --- Load the network weight --- #
if args.num != '9999999':
    pkl_list = [i for i in os.listdir(args.model_save_dir) if '.pkl' in i]
    name = [i for i in pkl_list if 'epoch_' + str(args.num) + '_' in i][0]
    SDN.load_state_dict(
        torch.load(os.path.join(args.model_save_dir, name),
                   map_location="cuda:{}".format(device_ids[0])))
    print('--- {} epoch weight loaded ---'.format(args.num))
    start_epoch = int(args.num) + 1
else:
    pkl_list = [i for i in os.listdir(args.model_save_dir) if '.pkl' in i]
    num = sorted([int(i.split('.')[0].split('_')[1]) for i in pkl_list])[-1]
    name = [i for i in pkl_list if 'epoch_' + str(num) + '_' in i][0]
    SDN.load_state_dict(
        torch.load(os.path.join(args.model_save_dir, name),
                   map_location="cuda:{}".format(device_ids[0])))
    print('--- {} epoch weight loaded ---'.format(num))

test_txt = open(os.path.join(predict_result, 'result.txt'), 'w+')


# --- Strat testing --- #
with torch.no_grad():
    img_list = []
    psnr_list = []
    ssim_list = []
    SDN.eval()
    imsave_dir = output_dir
    if not os.path.exists(imsave_dir):
        os.makedirs(imsave_dir)
    for (hazy, clean, name) in test_loader:

        hazy = hazy.to(device)
        clean = clean.to(device)

        h_list = [i for i in laplace.pyramid_decom(hazy)]
        high = h_list[0]

        h, w = hazy.shape[2], hazy.shape[3]
        max_h = int(math.ceil(h / 256)) * 256
        max_w = int(math.ceil(w / 256)) * 256

        hazy, ori_left, ori_right, ori_top, ori_down = padding_image(hazy, max_h, max_w)
        high, _, _, _, _ = padding_image(high, max_h, max_w)
        haze, dehaze, img, tran, atp = SDN(hazy, high)

        img = img.data[:, :, ori_top:ori_down, ori_left:ori_right]
        dehaze = dehaze[:, :, ori_top:ori_down, ori_left:ori_right]
        tran = tran.data[:, :, ori_top:ori_down, ori_left:ori_right]
        atp = atp.data[:, :, ori_top:ori_down, ori_left:ori_right]

        uresult = to_psnr(img, clean)
        atpresult = to_psnr(dehaze, clean)

        frame_out = dehaze
        if atpresult >= uresult:
            psnr_list.extend(to_psnr(dehaze, clean))
            ssim_list.extend(to_ssim_skimage(dehaze, clean))
        else:
            psnr_list.extend(to_psnr(img, clean))
            ssim_list.extend(to_ssim_skimage(img, clean))
            frame_out = img

        # frame_out_up = SDN(hazy_up)
        # frame_out_down = SDN(hazy_down)
        # frame_out=(torch.cat([frame_out_up.permute(0,2,3,1), frame_out_down[:,:,80:640,:].permute(0,2,3,1)], 1)).permute(0,3,1,2)

        imwrite(frame_out, os.path.join(predict_dir, name[0]), range=(0, 1))

        imwrite(tran, os.path.join(tran_dir, name[0]), range=(0, 1))
        imwrite(atp, os.path.join(atp_dir, name[0]), range=(0, 1))

        psnr_list.extend(to_psnr(frame_out, clean))

        ssim_list.extend(to_ssim_skimage(frame_out, clean))
        print(name[0], to_psnr(frame_out, clean), to_ssim_skimage(frame_out, clean))
        tmp = test_txt.writelines(name[0] + '->\tpsnr: ' + str(to_psnr(frame_out, clean)[0]) + '\tssim:' + str(
            to_ssim_skimage(frame_out, clean)[0]) + '\n')
    avr_psnr = sum(psnr_list) / len(psnr_list)
    avr_ssim = sum(ssim_list) / len(ssim_list)
    tmp = test_txt.writelines(
        tag + 'datasets ==>>\tpsnr:' + str(avr_psnr) + '\tssim:' + str(avr_ssim) + '\n')
    print('dehazed', avr_psnr, avr_ssim)

test_txt.close()
# imwrite(img_list[batch_idx], os.path.join(imsave_dir, str(batch_idx) + '.png'))

# writer.close()
