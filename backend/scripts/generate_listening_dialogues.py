"""生成 10 篇日常对话（A/B 一问一答）并预热全部 TTS 音频。

与 generate_listening_stories.py 的区别：内容不写 LLM——对话是功能型口语
（问好/借东西/点餐/问路…），手写 10 篇保证难度和格式 100% 受控，
validate_dialogue 逐篇把关（A 先开口、严格轮流、1-10 词/轮）。

用法（VPS 上）：
    cd /opt/MemoSeed/backend
    .venv/bin/python -m scripts.generate_listening_dialogues --username 轩轩

可选：
    --warm-only           跳过生成，只对已有对话补预热音频
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.listening_story import ListeningStory
from app.models.user import User
from app.services.listening_stories import (
    DIALOGUE_THEME,
    resolve_dialogue_b_voices,
    validate_dialogue,
    warm_story_audio,
)
from scripts.generate_listening_stories import (
    resolve_user,
    resolve_voices,
    tts_settings_factory,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("generate_listening_dialogues")

DIALOGUES: list[dict] = [
    {
        "title_en": "Say Hello",
        "title_zh": "打招呼问好",
        "sentences": [
            {"speaker": "A", "en": "Hello! What's your name?", "zh": "你好！你叫什么名字？"},
            {"speaker": "B", "en": "Hi! My name is Xiaoming.", "zh": "嗨！我叫小明。"},
            {"speaker": "A", "en": "Nice to meet you, Xiaoming.", "zh": "很高兴认识你，小明。"},
            {"speaker": "B", "en": "Nice to meet you too.", "zh": "我也很高兴认识你。"},
            {"speaker": "A", "en": "How old are you?", "zh": "你几岁了？"},
            {"speaker": "B", "en": "I am seven years old.", "zh": "我七岁了。"},
            {"speaker": "A", "en": "What class are you in?", "zh": "你在哪个班？"},
            {"speaker": "B", "en": "I am in Class Two, Grade One.", "zh": "我在一年级二班。"},
            {"speaker": "A", "en": "Do you like our school?", "zh": "你喜欢我们的学校吗？"},
            {"speaker": "B", "en": "Yes, I like it very much.", "zh": "是的，我非常喜欢。"},
            {"speaker": "A", "en": "Let's play together after class.", "zh": "下课后我们一起玩吧。"},
            {"speaker": "B", "en": "Great! See you later.", "zh": "太好了！待会儿见。"},
        ],
    },
    {
        "title_en": "Borrow Things",
        "title_zh": "借东西",
        "sentences": [
            {"speaker": "A", "en": "Excuse me, may I borrow your pencil?", "zh": "打扰一下，我可以借你的铅笔吗？"},
            {"speaker": "B", "en": "Sure. Here you are.", "zh": "当然可以。给你。"},
            {"speaker": "A", "en": "Thank you very much.", "zh": "非常感谢你。"},
            {"speaker": "B", "en": "You're welcome.", "zh": "不客气。"},
            {"speaker": "A", "en": "Oh no, my eraser is lost.", "zh": "哦不，我的橡皮丢了。"},
            {"speaker": "B", "en": "Don't worry. You can use mine.", "zh": "别担心。你可以用我的。"},
            {"speaker": "A", "en": "You are so kind.", "zh": "你真好。"},
            {"speaker": "B", "en": "We are good friends.", "zh": "我们是好朋友。"},
            {"speaker": "A", "en": "I will give it back tomorrow.", "zh": "我明天还给你。"},
            {"speaker": "B", "en": "No hurry. Take your time.", "zh": "不着急，慢慢来。"},
            {"speaker": "A", "en": "Can I borrow a ruler too?", "zh": "我也能借一把尺子吗？"},
            {"speaker": "B", "en": "Of course. Here it is.", "zh": "当然可以。给你。"},
        ],
    },
    {
        "title_en": "At the Restaurant",
        "title_zh": "在餐厅点餐",
        "sentences": [
            {"speaker": "A", "en": "Good evening! A table for two, please.", "zh": "晚上好！请给我们一张两人桌。"},
            {"speaker": "B", "en": "This way, please. Here is the menu.", "zh": "这边请。这是菜单。"},
            {"speaker": "A", "en": "Thank you. What do you have today?", "zh": "谢谢。你们今天有什么菜？"},
            {"speaker": "B", "en": "We have rice, noodles and fish.", "zh": "我们有米饭、面条和鱼。"},
            {"speaker": "A", "en": "I would like some rice and fish.", "zh": "我想要米饭和鱼。"},
            {"speaker": "B", "en": "Anything to drink?", "zh": "要喝点什么吗？"},
            {"speaker": "A", "en": "A glass of orange juice, please.", "zh": "请给我一杯橙汁。"},
            {"speaker": "B", "en": "OK. Please wait a minute.", "zh": "好的。请稍等。"},
            {"speaker": "A", "en": "The fish is yummy!", "zh": "鱼真好吃！"},
            {"speaker": "B", "en": "I am glad you like it.", "zh": "很高兴你喜欢。"},
            {"speaker": "A", "en": "How much is it?", "zh": "多少钱？"},
            {"speaker": "B", "en": "Fifty yuan, please.", "zh": "五十元。"},
            {"speaker": "A", "en": "Here is the money. Thank you!", "zh": "给你钱。谢谢！"},
            {"speaker": "B", "en": "You're welcome. Come again!", "zh": "不客气。欢迎再来！"},
        ],
    },
    {
        "title_en": "Buy a Toy Car",
        "title_zh": "买玩具车",
        "sentences": [
            {"speaker": "A", "en": "Good afternoon! Can I help you?", "zh": "下午好！我能帮你吗？"},
            {"speaker": "B", "en": "Yes, I want a toy car.", "zh": "是的，我想要一辆玩具车。"},
            {"speaker": "A", "en": "What color do you like?", "zh": "你喜欢什么颜色？"},
            {"speaker": "B", "en": "I like the red one.", "zh": "我喜欢红色的那辆。"},
            {"speaker": "A", "en": "Here you are. It runs fast.", "zh": "给你。它跑得很快。"},
            {"speaker": "B", "en": "Wow, cool! How much is it?", "zh": "哇，好酷！多少钱？"},
            {"speaker": "A", "en": "It is thirty yuan.", "zh": "三十元。"},
            {"speaker": "B", "en": "Oh, that is too much.", "zh": "哦，太贵了。"},
            {"speaker": "A", "en": "This blue one is only twenty yuan.", "zh": "这辆蓝色的只要二十元。"},
            {"speaker": "B", "en": "OK, I will take the blue one.", "zh": "好的，我买蓝色的。"},
            {"speaker": "A", "en": "Here is your toy car.", "zh": "给你玩具车。"},
            {"speaker": "B", "en": "Thank you! Goodbye!", "zh": "谢谢！再见！"},
        ],
    },
    {
        "title_en": "Ask the Way",
        "title_zh": "问路",
        "sentences": [
            {"speaker": "A", "en": "Excuse me, where is the library?", "zh": "打扰一下，图书馆在哪里？"},
            {"speaker": "B", "en": "It is next to the park.", "zh": "它在公园旁边。"},
            {"speaker": "A", "en": "Is it far from here?", "zh": "离这里远吗？"},
            {"speaker": "B", "en": "No, it is near.", "zh": "不远，很近。"},
            {"speaker": "A", "en": "How can I get there?", "zh": "我怎么去呢？"},
            {"speaker": "B", "en": "Go straight and turn left.", "zh": "直走然后左转。"},
            {"speaker": "A", "en": "Then what?", "zh": "然后呢？"},
            {"speaker": "B", "en": "You can see a big red building.", "zh": "你会看到一座红色的大楼。"},
            {"speaker": "A", "en": "Is the library in that building?", "zh": "图书馆就在那座楼里吗？"},
            {"speaker": "B", "en": "Yes, it is on the first floor.", "zh": "是的，它在一楼。"},
            {"speaker": "A", "en": "Thank you so much!", "zh": "太感谢你了！"},
            {"speaker": "B", "en": "You are welcome. Bye!", "zh": "不客气。再见！"},
        ],
    },
    {
        "title_en": "Talk About Weather",
        "title_zh": "谈论天气",
        "sentences": [
            {"speaker": "A", "en": "Good morning! How is the weather today?", "zh": "早上好！今天天气怎么样？"},
            {"speaker": "B", "en": "It is sunny and warm.", "zh": "晴朗又暖和。"},
            {"speaker": "A", "en": "Great! Can we go to the park?", "zh": "太好了！我们能去公园吗？"},
            {"speaker": "B", "en": "Sure. But it may rain later.", "zh": "可以。但晚点可能会下雨。"},
            {"speaker": "A", "en": "Really? Should I take an umbrella?", "zh": "真的吗？我要带伞吗？"},
            {"speaker": "B", "en": "Yes, take a small one.", "zh": "是的，带一把小伞。"},
            {"speaker": "A", "en": "What about tomorrow?", "zh": "明天呢？"},
            {"speaker": "B", "en": "It will be windy and cold.", "zh": "会刮风变冷。"},
            {"speaker": "A", "en": "Then I will wear my coat.", "zh": "那我要穿外套。"},
            {"speaker": "B", "en": "Good idea. Let's go now!", "zh": "好主意。我们现在走吧！"},
            {"speaker": "A", "en": "Wait for me!", "zh": "等等我！"},
            {"speaker": "B", "en": "Hurry up! The bus is coming.", "zh": "快点！公交车来了。"},
        ],
    },
    {
        "title_en": "A Phone Call",
        "title_zh": "打电话",
        "sentences": [
            {"speaker": "A", "en": "Hello! This is Xiaoming.", "zh": "你好！我是小明。"},
            {"speaker": "B", "en": "Hi, Xiaoming! This is Lily.", "zh": "嗨，小明！我是莉莉。"},
            {"speaker": "A", "en": "Can you come out to play?", "zh": "你能出来玩吗？"},
            {"speaker": "B", "en": "Sorry, I am doing my homework.", "zh": "对不起，我在写作业。"},
            {"speaker": "A", "en": "What about this afternoon?", "zh": "那今天下午呢？"},
            {"speaker": "B", "en": "OK. What time?", "zh": "可以。几点？"},
            {"speaker": "A", "en": "How about three o'clock?", "zh": "三点怎么样？"},
            {"speaker": "B", "en": "Good. Where shall we meet?", "zh": "好的。我们在哪里见面？"},
            {"speaker": "A", "en": "At the school gate.", "zh": "在学校门口。"},
            {"speaker": "B", "en": "All right. See you at three.", "zh": "好的。三点见。"},
            {"speaker": "A", "en": "See you! Don't be late!", "zh": "再见！别迟到哦！"},
            {"speaker": "B", "en": "I won't. Bye!", "zh": "不会的。拜拜！"},
        ],
    },
    {
        "title_en": "See the Doctor",
        "title_zh": "看医生",
        "sentences": [
            {"speaker": "A", "en": "Good morning, doctor.", "zh": "早上好，医生。"},
            {"speaker": "B", "en": "Good morning. What's wrong with you?", "zh": "早上好。你怎么了？"},
            {"speaker": "A", "en": "I have a headache and a cough.", "zh": "我头疼还咳嗽。"},
            {"speaker": "B", "en": "Let me have a look. Open your mouth.", "zh": "让我看看。张开嘴。"},
            {"speaker": "A", "en": "Is it serious, doctor?", "zh": "严重吗，医生？"},
            {"speaker": "B", "en": "Don't worry. It's just a cold.", "zh": "别担心。只是感冒。"},
            {"speaker": "A", "en": "What should I do?", "zh": "我该怎么办？"},
            {"speaker": "B", "en": "Take this medicine three times a day.", "zh": "这个药一天吃三次。"},
            {"speaker": "A", "en": "Should I stay in bed?", "zh": "我需要卧床休息吗？"},
            {"speaker": "B", "en": "Yes, have a good rest and drink warm water.", "zh": "是的，好好休息，多喝温水。"},
            {"speaker": "A", "en": "Thank you, doctor.", "zh": "谢谢你，医生。"},
            {"speaker": "B", "en": "You will be fine soon.", "zh": "你很快就会好的。"},
        ],
    },
    {
        "title_en": "Talk About Hobbies",
        "title_zh": "谈论爱好",
        "sentences": [
            {"speaker": "A", "en": "What do you like to do after school?", "zh": "放学后你喜欢做什么？"},
            {"speaker": "B", "en": "I like to draw pictures. And you?", "zh": "我喜欢画画。你呢？"},
            {"speaker": "A", "en": "I like to play football.", "zh": "我喜欢踢足球。"},
            {"speaker": "B", "en": "Are you good at it?", "zh": "你踢得好吗？"},
            {"speaker": "A", "en": "Yes, I can run very fast.", "zh": "是的，我跑得很快。"},
            {"speaker": "B", "en": "Can you teach me to play?", "zh": "你能教我踢吗？"},
            {"speaker": "A", "en": "Of course! Let's play on Friday.", "zh": "当然！我们周五一起玩。"},
            {"speaker": "B", "en": "Great! What is your favorite color?", "zh": "太好了！你最喜欢什么颜色？"},
            {"speaker": "A", "en": "I like blue best. What about you?", "zh": "我最喜欢蓝色。你呢？"},
            {"speaker": "B", "en": "I like pink. It is pretty.", "zh": "我喜欢粉色。它很漂亮。"},
            {"speaker": "A", "en": "Can you draw a pink flower for me?", "zh": "你能给我画一朵粉色的花吗？"},
            {"speaker": "B", "en": "Sure! I will draw one tomorrow.", "zh": "当然！我明天就画一朵。"},
        ],
    },
    {
        "title_en": "Weekend Plans",
        "title_zh": "周末计划",
        "sentences": [
            {"speaker": "A", "en": "The weekend is coming!", "zh": "周末要到了！"},
            {"speaker": "B", "en": "Yes! What are you going to do?", "zh": "是的！你打算做什么？"},
            {"speaker": "A", "en": "I am going to visit my grandma.", "zh": "我要去看我奶奶。"},
            {"speaker": "B", "en": "Does she live far away?", "zh": "她住得远吗？"},
            {"speaker": "A", "en": "No, she lives near the river.", "zh": "不远，她住在河边。"},
            {"speaker": "B", "en": "What will you do there?", "zh": "你会在那里做什么？"},
            {"speaker": "A", "en": "I will help her clean the room.", "zh": "我会帮她打扫房间。"},
            {"speaker": "B", "en": "You are a good boy!", "zh": "你真是个好孩子！"},
            {"speaker": "A", "en": "Thank you. And your weekend?", "zh": "谢谢。你的周末呢？"},
            {"speaker": "B", "en": "I will go swimming with my dad.", "zh": "我要和爸爸去游泳。"},
            {"speaker": "A", "en": "Wow, that sounds fun!", "zh": "哇，听起来很有趣！"},
            {"speaker": "B", "en": "Do you want to come with us?", "zh": "你想和我们一起去吗？"},
            {"speaker": "A", "en": "Yes, please! I love swimming.", "zh": "想啊！我爱游泳。"},
            {"speaker": "B", "en": "OK, see you on Saturday!", "zh": "好，周六见！"},
        ],
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description="生成日常对话听力材料并预热 TTS 音频")
    parser.add_argument("--username", required=True, help="孩子账号用户名")
    parser.add_argument("--warm-only", action="store_true", help="只对已有对话补预热音频")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        user = resolve_user(db, args.username)
        logger.info("目标用户: %s (%s)", user.username, user.id)

        if not args.warm_only:
            existing_titles = {
                row[0]
                for row in db.execute(
                    select(ListeningStory.title).where(ListeningStory.user_id == user.id)
                ).all()
            }
            created = 0
            for dialogue in DIALOGUES:
                validated = validate_dialogue(dialogue)
                if not validated:
                    logger.error("对话校验失败（不应发生，内容手写在脚本里）: %s", dialogue["title_zh"])
                    continue
                if any(validated["title"] in title or dialogue["title_zh"] in title for title in existing_titles):
                    logger.info("已存在，跳过: %s", validated["title"])
                    continue
                story = ListeningStory(
                    user_id=user.id,
                    title=validated["title"],
                    theme=DIALOGUE_THEME,
                    sentences=validated["sentences"],
                )
                db.add(story)
                created += 1
                logger.info("新增: %s（%d 轮）", validated["title"], len(validated["sentences"]))
            db.commit()
            logger.info("对话生成完成：新增 %d 篇", created)

        en_voice, zh_voice, speech_rate = resolve_voices(db, user.id)
        en_voice_b, zh_voice_b = resolve_dialogue_b_voices(en_voice, zh_voice)
        logger.info("TTS: A 音色 en=%s zh=%s | B 音色 en=%s zh=%s | rate=%d",
                    en_voice, zh_voice, en_voice_b, zh_voice_b, speech_rate)
        factory = tts_settings_factory(db, user.id, speech_rate)

        all_dialogues = db.scalars(
            select(ListeningStory)
            .where(ListeningStory.user_id == user.id, ListeningStory.theme == DIALOGUE_THEME)
            .order_by(ListeningStory.created_at.asc())
        ).all()
        totals = {"cached": 0, "generated": 0, "failed": 0, "total": 0}
        for story in all_dialogues:
            stats = warm_story_audio(
                story, en_voice, zh_voice, speech_rate, factory, en_voice_b, zh_voice_b,
            )
            for key in totals:
                totals[key] += stats[key]
            logger.info(
                "  预热 [%s]: cached=%d generated=%d failed=%d",
                story.title, stats["cached"], stats["generated"], stats["failed"],
            )
        logger.info(
            "全部完成: 对话 %d 篇, 音频 total=%d cached=%d generated=%d failed=%d",
            len(all_dialogues), totals["total"], totals["cached"], totals["generated"], totals["failed"],
        )
        if totals["failed"]:
            logger.warning("有 %d 条音频生成失败，重跑本脚本可补齐（已缓存的不会重复生成）", totals["failed"])
    finally:
        db.close()


if __name__ == "__main__":
    main()
