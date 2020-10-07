import torch
import MinkowskiEngine as ME
from functools import partial

from torch_points3d.modules.MinkowskiEngine.api_modules import ResNetBase, ResNetDown
from .gsdn_results import GSDNLayerPrediction


class GSDNUp(ResNetBase):
    """
    Transpose convolution block for GSDN. Returns new tensor as well as box predictions and sparsity_score
    Returns the result of the transposed convolution only when
    it is a strided convolution.
    """

    CONVOLUTION_IN = partial(ME.MinkowskiConvolutionTranspose, generate_new_coords=True)

    def __init__(
        self, up_conv_nn=[], kernel_size=2, stride=2, N=1, nb_anchors=13, tau=0.3, num_classes=None, **kwargs,
    ):
        super().__init__(
            down_conv_nn=up_conv_nn, kernel_size=kernel_size, stride=stride, N=N, **kwargs,
        )

        self.Sparsity = torch.nn.Linear(up_conv_nn[0], 1, bias=False)
        self.Detector = torch.nn.Linear(
            up_conv_nn[0], nb_anchors * (num_classes + 7)
        )  # 3 center offset, 3 size offset and 1 objectness
        self.tau = tau
        self.stride = stride
        self.nb_anchors = nb_anchors

    def forward(self, x, skip):
        """
        Takes an input tensor and a skip connection if any and performs box prediction followed by transposed convolutions
        If skip is provided, makes the union of the input and the skip.
        """
        if skip is not None:
            union = ME.MinkowskiUnion()
            inp = union(x, skip)
        else:
            inp = x

        # Compute predictions for that layer
        boxes = self.Detector(inp.feats)
        sparsity_logits = self.Sparsity(inp.feats).squeeze(-1)
        boxes = GSDNLayerPrediction.create_from_logits(inp, boxes, self.nb_anchors, sparsity_logits)

        # Prune
        sparsity_proba = torch.sigmoid(sparsity_logits)
        keep = (sparsity_proba > self.tau).cpu()
        if torch.sum(keep) > 1000:  # Too many are kept initially
            keep[:] = False
            topk = torch.topk(sparsity_proba, 1000)[1]
            keep[topk] = True
        pruned = ME.MinkowskiPruning()(inp, keep)

        # Conv transpose
        if self.stride > 1:
            out_tensor = super().forward(pruned)
        else:
            out_tensor = pruned
        return out_tensor, boxes