public class TooManyParams {

    public void sixParams(int a, int b, int c, int d, int e, int f) {
        // 6 parameters > 5 → TooManyParameters triggered
        System.out.println(a + b + c + d + e + f);
    }

    public void fiveParams(int a, int b, int c, int d, int e) {
        // 5 parameters — exactly at limit, should NOT trigger
        System.out.println(a + b + c + d + e);
    }
}
