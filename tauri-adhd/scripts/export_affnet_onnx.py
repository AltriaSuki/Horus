"""
Export AFFNet from PyTorch .pth.tar to ONNX format.
Usage: python scripts/export_affnet_onnx.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'itraker', 'GazeTrack'))

import torch
from model.AFFNet import AFFNet

PTH_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'model', 'train_model', 'affnet.pth.tar')
ONNX_PATH = os.path.join(os.path.dirname(__file__), '..', 'models', 'affnet.onnx')

def main():
    print(f"Loading weights from: {os.path.abspath(PTH_PATH)}")
    checkpoint = torch.load(PTH_PATH, map_location='cpu', weights_only=False)

    model = AFFNet()
    # Handle different checkpoint formats
    if 'state_dict' in checkpoint:
        state = checkpoint['state_dict']
    elif 'model_state_dict' in checkpoint:
        state = checkpoint['model_state_dict']
    else:
        state = checkpoint

    # Strip "module." prefix from DataParallel
    cleaned = {}
    for k, v in state.items():
        cleaned[k.replace('module.', '')] = v
    model.load_state_dict(cleaned)
    model.eval()

    print(f"Model loaded with {sum(p.numel() for p in model.parameters())} parameters")

    # Dummy inputs matching Python preprocessing
    left_eye  = torch.randn(1, 3, 112, 112)
    right_eye = torch.randn(1, 3, 112, 112)
    face      = torch.randn(1, 3, 224, 224)
    rect      = torch.randn(1, 12)

    # Quick forward pass to verify
    with torch.no_grad():
        out = model(left_eye, right_eye, face, rect)
        print(f"Forward pass OK, output shape: {out.shape}, values: {out}")

    # Export
    print(f"Exporting to: {os.path.abspath(ONNX_PATH)}")
    torch.onnx.export(
        model,
        (left_eye, right_eye, face, rect),
        ONNX_PATH,
        input_names=['left_eye', 'right_eye', 'face', 'rect'],
        output_names=['gaze'],
        dynamic_axes=None,
        opset_version=11,
        do_constant_folding=True,
    )
    
    onnx_size = os.path.getsize(ONNX_PATH)
    data_path = ONNX_PATH + '.data'
    data_size = os.path.getsize(data_path) if os.path.exists(data_path) else 0
    print(f"Export done! ONNX: {onnx_size} bytes, data: {data_size} bytes")

    # Verify with ONNX Runtime
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(ONNX_PATH)
        result = sess.run(None, {
            'left_eye': left_eye.numpy(),
            'right_eye': right_eye.numpy(),
            'face': face.numpy(),
            'rect': rect.numpy(),
        })
        print(f"ONNX Runtime verification OK, output: {result[0]}")
    except ImportError:
        print("onnxruntime not installed, skipping verification")

if __name__ == '__main__':
    main()
