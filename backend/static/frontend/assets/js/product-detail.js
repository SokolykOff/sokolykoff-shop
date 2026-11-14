/* Переопределяет product-detail.js из пакета frontend: добавляет структурированную AI-сводку
   отзывов и её обновление после публикации нового отзыва. */
var mix = {
    computed: {
        tags () {
            if (!this.product?.tags) return []
            return this.product.tags
        },
        ai () {
            return this.product?.aiSummary || null
        },
        aiStarsStyle () {
            const avg = this.ai?.avg || 0
            return { '--p': Math.max(0, Math.min(100, avg / 5 * 100)) + '%' }
        }
    },
    methods: {
        aiSourceLabel (source) {
            return { local: 'Локальный AI-движок', openai: 'OpenAI', gemini: 'Gemini', compat: 'LLM API' }[source] || 'AI'
        },
        changeCount (value) {
            this.count = this.count + value
            if (this.count < 1) this.count = 1
        },
        getProduct() {
            const productId = location.pathname.startsWith('/product/')
            ? Number(location.pathname.replace('/product/', '').replace('/', ''))
            : null
            this.getData(`/api/product/${productId}/`).then(data => {
                this.product = {
                    ...this.product,
                    ...data
                }
                if(data.images.length)
                    this.activePhoto = 0
            }).catch(() => {
                this.product = {}
                console.warn('Ошибка при получении товара')
            })
        },
        submitReview () {
            this.postData(`/api/product/${this.product.id}/reviews/`, {
                author: this.review.author,
                email: this.review.email,
                text: this.review.text,
                rate: this.review.rate
            }).then(({data}) => {
                this.product.reviews = data
                alert('Отзыв опубликован')
                this.review.author = ''
                this.review.email = ''
                this.review.text = ''
                this.review.rate = 5
                this.getProduct() // подтягиваем пересчитанную AI-сводку
            }).catch(() => {
                console.warn('Ошибка при публикации отзыва')
            })
        },
        setActivePhoto(index) {
            this.activePhoto = index
        }
    },
    mounted () {
        this.getProduct();
    },
    data() {
        return {
            product : {},
            activePhoto: 0,
            count: 1,
            review: {
                author: '',
                email: '',
                text: '',
                rate: 5
            }
        }
    },
}
