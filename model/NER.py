import re
import os
import json

import torch
import onnxruntime

from transformers import pipeline as transformers_pipeline
from transformers import AutoTokenizer
from transformers import AutoModelForTokenClassification
from optimum.pipelines import pipeline as onnx_pipeline
from optimum.onnxruntime import ORTModelForTokenClassification

from model.Word import Word
from helper.LogHelper import LogHelper
from helper.TextHelper import TextHelper
from helper.ProgressHelper import ProgressHelper

class NER:

    TASK_MODES = type("GClass", (), {})()
    TASK_MODES.QUICK = 10
    TASK_MODES.ACCURACY = 20

    MODEL_PATH_CPU= "resource\\kg_ner_ja_onnx_cpu"
    MODEL_PATH_GPU = "resource\\numind_NuNER_multilingual_v0_1_best"
    LINE_SIZE_PER_GROUP = 256

    RE_SPLIT_BY_PUNCTUATION = re.compile(
        rf"[" +
        rf"{TextHelper.GENERAL_PUNCTUATION[0]}-{TextHelper.GENERAL_PUNCTUATION[1]}" +
        rf"{TextHelper.CJK_SYMBOLS_AND_PUNCTUATION[0]}-{TextHelper.CJK_SYMBOLS_AND_PUNCTUATION[1]}" +
        rf"{TextHelper.HALFWIDTH_AND_FULLWIDTH_FORMS[0]}-{TextHelper.HALFWIDTH_AND_FULLWIDTH_FORMS[1]}" +
        rf"{TextHelper.LATIN_PUNCTUATION_BASIC_1[0]}-{TextHelper.LATIN_PUNCTUATION_BASIC_1[1]}" +
        rf"{TextHelper.LATIN_PUNCTUATION_BASIC_2[0]}-{TextHelper.LATIN_PUNCTUATION_BASIC_2[1]}" +
        rf"{TextHelper.LATIN_PUNCTUATION_BASIC_3[0]}-{TextHelper.LATIN_PUNCTUATION_BASIC_3[1]}" +
        rf"{TextHelper.LATIN_PUNCTUATION_BASIC_4[0]}-{TextHelper.LATIN_PUNCTUATION_BASIC_4[1]}" +
        rf"{TextHelper.LATIN_PUNCTUATION_GENERAL[0]}-{TextHelper.LATIN_PUNCTUATION_GENERAL[1]}" +
        rf"{TextHelper.LATIN_PUNCTUATION_SUPPLEMENTAL[0]}-{TextHelper.LATIN_PUNCTUATION_SUPPLEMENTAL[1]}" +
        rf"・♥]+"
    )

    NER_TYPES = {
        "PER": "PER",       # 表示人名，如"张三"、"约翰·多伊"等。
        "ORG": "ORG",       # 表示组织，如"联合国"、"苹果公司"等。
        "LOC": "LOC",       # 表示地点，通常指非地理政治实体的地点，如"房间"、"街道"等。
        "INS": "INS",       # 表示设施，如"医院"、"学校"、"机场"等。
        "PRD": "PRD",       # 表示产品，如"iPhone"、"Windows操作系统"等。
        "EVT": "EVT",       # 表示事件，如"奥运会"、"地震"等。

        "人名": "PER",
        "法人名": "ORG",
        "政治的組織名": "ORG",
        "その他の組織名": "ORG",
        "地名": "LOC",
        "施設名": "INS",
        "製品名": "PRD",
        "イベント名": "EVT",
    }

    def __init__(self):
        self.blacklist = ""

        # 在支持的设备和模型上启用 GPU 加速
        if torch.cuda.is_available() and LogHelper.is_debug():
            self.model = AutoModelForTokenClassification.from_pretrained(
                self.MODEL_PATH_GPU,
                torch_dtype = torch.float16,
                local_files_only = True,
            )

            self.tokenizer = AutoTokenizer.from_pretrained(
                self.MODEL_PATH_GPU,
                truncation = True,
                max_length = 512,
                model_max_length = 512,
                local_files_only = True,
            )

            self.classifier = transformers_pipeline(
                "token-classification",
                model = self.model,
                tokenizer = self.tokenizer,
                device = "cuda",
                batch_size = 256,
                aggregation_strategy = "simple",
            )
        else:
            self.classifier = onnx_pipeline(
                "token-classification",
                model = ORTModelForTokenClassification.from_pretrained(
                    self.MODEL_PATH_CPU,
                    local_files_only = True,
                ),
                tokenizer = AutoTokenizer.from_pretrained(
                    self.MODEL_PATH_CPU,
                    truncation = True,
                    max_length = 512,
                    model_max_length = 512,
                    local_files_only = True,
                ),
                device = "cpu",
                batch_size = min(8, os.cpu_count()),
                aggregation_strategy = "simple", 
            )

    # 释放资源
    def release(self):
        if self.classifier:
            del self.classifier
        if self.model:
            del self.model
        if self.tokenizer:
            del self.tokenizer

    # 从指定路径加载黑名单文件内容
    def load_blacklist(self, filepath):
        try:
            with open(filepath, "r", encoding = "utf-8") as file:
                data = json.load(file)

                self.blacklist = ""
                for k, v in enumerate(data):
                    self.blacklist = self.blacklist + v + "\n"
        except Exception as e:
            LogHelper.error(f"加载配置文件时发生错误 - {LogHelper.get_trackback(e)}")

    # 生成词语 
    def generate_words(self, score, surface, ner_type):
        surface = TextHelper.strip_not_japanese(surface)
        surface = surface.strip("の")

        # 有效性检查
        if not TextHelper.is_valid_japanese_word(surface, self.blacklist):
            return []

        # 过滤非实体
        if ner_type not in self.NER_TYPES:
            return []

        words = [Word()]
        words[0] = Word()
        words[0].count = 1
        words[0].score = score
        words[0].surface = surface
        words[0].ner_type = ner_type

        return words

    # 查找 NER 实体
    def search_for_entity(self, full_lines):
        words = []
        full_lines_chunked = [
            full_lines[i : i + self.LINE_SIZE_PER_GROUP]
            for i in range(0, len(full_lines), self.LINE_SIZE_PER_GROUP)
        ]

        LogHelper.print()
        with ProgressHelper.get_progress() as progress:
            pid = progress.add_task("查找 NER 实体", total = None)

            for k, lines in enumerate(full_lines_chunked):
                self.classifier.call_count = 0 # 防止出现应使用 dateset 的提示
                for i, doc in enumerate(self.classifier(lines)):
                    for token in doc:
                        surfaces = re.split(self.RE_SPLIT_BY_PUNCTUATION, token.get("word").replace(" ", ""))
                        for surface in surfaces:
                            score = token.get("score")
                            entity_group = self.NER_TYPES.get(token.get("entity_group"), "")
                            words.extend(self.generate_words(score, surface, entity_group))

                progress.update(pid, advance = 1, total = len(full_lines_chunked))

        self.release()
        LogHelper.print()
        LogHelper.info(f"[查找 NER 实体] 已完成 ...")

        return words

    # 通过 词语形态 校验词语
    def lemmatize_words_by_morphology(self, words, full_lines):
        words_ex = []
        for word in words:
            # 以下步骤只对角色实体进行
            if not word.ner_type == self.NER_TYPES.get("PER"):
                continue

            # 前面的步骤中已经移除了首尾的 の，如果还有，那就是 AのB 的形式，跳过
            if "の" in word.surface:
                continue

            # 如果开头结尾都是汉字，跳过
            if TextHelper.is_cjk(word.surface[0]) and TextHelper.is_cjk(word.surface[-1]):
                continue

            # 拆分词根
            tokens = TextHelper.extract_japanese(word.surface)

            # 如果已不能再拆分，跳过
            if len(tokens) == 1:
                continue
          
            # 获取词根，获取成功则更新词语
            roots = []
            for k, v in enumerate(tokens):
                if TextHelper.is_valid_japanese_word(v, self.blacklist):
                    word_ex = Word()
                    word_ex.count = word.count
                    word_ex.score = word.score
                    word_ex.surface = v
                    word_ex.ner_type = word.ner_type                
                    word_ex.set_context(word_ex.surface, full_lines)

                    roots.append(v)
                    words_ex.append(word_ex)

            if len(roots) > 0:
                LogHelper.info(f"通过 [green]词语形态[/] 还原词根 - {word.ner_type} - {word.surface} [green]->[/] {' / '.join(roots)}")               
                word.ner_type = ""

        # 合并拆分出来的词语
        words.extend(words_ex)
        return words

    # 通过 出现次数 还原词根
    def lemmatize_words_by_count(self, words, full_lines):
        words_map = {}
        for word in words:
            key = (word.ner_type, "".join(word.context))

            if key not in words_map:
                words_map[key] = word
                continue
            else:
                ex_word = words_map[key]

            if word.count != ex_word.count and abs(word.count - ex_word.count) / max(word.count, ex_word.count, 1) > 0.05:
                continue

            if (word.surface in ex_word.surface or ex_word.surface in word.surface) and word.surface != ex_word.surface:
                if len(word.surface) > len(ex_word.surface):
                    LogHelper.info(f"通过 [green]出现次数[/] 还原词根 - {word.ner_type} - {word.surface} [green]->[/] {ex_word.surface}")
                    words_map[key] = word
                else:
                    LogHelper.info(f"通过 [green]出现次数[/] 还原词根 - {word.ner_type} - {ex_word.surface} [green]->[/] {word.surface}")
                    words_map[key] = ex_word

        # 根据 word_map 更新words
        updated_words = []
        for word in words:
            updated_words.append(words_map[(word.ner_type, "".join(word.context))])

        # 更新上下文
        for word in updated_words:
            word.set_context(word.surface, full_lines)

        return updated_words